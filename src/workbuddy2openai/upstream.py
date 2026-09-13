"""Upstream translation: SSE parsing, stream aggregation, gate retry.

Owns everything between the OpenAI request the converter received and the
/v2/chat/completions call the backend accepts: the SSE line parser shared by
both relay and aggregator, the non-stream aggregation, the error-code
extraction, and the one-retry fallback over the desktop-token path.
"""

from __future__ import annotations

import json
import os
import re
import time

import httpx
from fastapi import HTTPException

DIRECT_KEY_BACKEND = "https://www.codebuddy.ai"

CN_MODELS = [
    "glm-5.2", "glm-5.1", "glm-5v-turbo",
    "kimi-k2.7", "kimi-k2.6", "kimi-k2.5",
    "deepseek-v4-pro", "deepseek-v4-flash",
    "minimax-m3-pay", "hy3-preview-agent", "auto",
]

INTL_MODELS = [
    "auto", "hy3", "hy4-preview", "glm-5.3", "glm-5.2", "glm-5.1", "glm-5v-turbo",
    "minimax-m3", "kimi-k3", "kimi-k2.7", "kimi-k2.6",
    "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4.1-flash",
    "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gemini-3.1-pro",
]

DEFAULT_MODELS = CN_MODELS

PASSTHROUGH_BODY_KEYS = {
    "model", "messages", "tools", "tool_choice", "temperature",
    "max_tokens", "max_completion_tokens", "top_p", "stream",
    "stream_options", "stop", "presence_penalty", "frequency_penalty",
    "n", "response_format", "seed", "user", "reasoning_effort",
    "verbosity", "reasoning_summary",
}

TOKEN_PATH_RETRY_CODES = {6004, 11128}

_LOG_SINK = None


def set_log_sink(sink) -> None:
    global _LOG_SINK
    _LOG_SINK = sink


def _log(msg: str) -> None:
    if _LOG_SINK is not None:
        _LOG_SINK(msg)


def _truncate(s: str, n: int = 80) -> str:
    s = str(s).replace("\n", " ").strip()
    return s[:n] + ("…" if len(s) > n else "")


def _parse_sse_data(data: str | bytes) -> dict | None:
    """Parse one SSE data payload; None for [DONE], comments and undecodable JSON.

    Termination is the caller's concern: [DONE] returns None rather than
    stopping the iteration, so the byte-forwarding relay can keep reading.
    """
    if isinstance(data, bytes):
        data = data.strip()
        if data == b"[DONE]":
            return None
        try:
            data = data.decode("utf-8", "replace")
        except Exception:
            return None
    else:
        data = data.strip()
        if data == "[DONE]":
            return None
    try:
        return json.loads(data)
    except Exception:
        return None


async def sse_events(response: httpx.Response):
    async for line in response.aiter_lines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        event = _parse_sse_data(line[5:])
        if event is not None:
            yield event


async def _collect_stream(response: httpx.Response) -> dict:
    """Aggregate the backend's OpenAI SSE stream into one chat.completion object.

    Merges every chunk's delta (content / tool_calls) and takes usage /
    finish_reason from the stream.
    """
    content_parts: list[str] = []
    tool_calls: dict[int, dict] = {}
    model: str | None = None
    finish_reason: str | None = None
    usage: dict | None = None

    async for chunk in sse_events(response):
        model = chunk.get("model") or model
        if chunk.get("usage"):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content_parts.append(delta["content"])
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tool_calls.setdefault(idx, {"id": None, "name": None, "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]

    tcs = None
    if tool_calls:
        tcs = [
            {"id": v["id"], "type": "function",
             "function": {"name": v["name"], "arguments": v["arguments"]}}
            for _, v in sorted(tool_calls.items())
        ]
        finish_reason = finish_reason or "tool_calls"

    message = {"role": "assistant", "content": "".join(content_parts) or None}
    if tcs:
        message["tool_calls"] = tcs
    return {
        "id": "chatcmpl-" + os.urandom(12).hex(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model or "unknown",
        "choices": [{"index": 0, "message": message,
                     "finish_reason": finish_reason or "stop"}],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _safe_err_raw(raw: bytes, status: int) -> dict:
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {"error": {"message": raw.decode("utf-8", "replace")[:500], "type": "upstream_error", "code": status}}


def _err_code(detail: dict) -> int | None:
    """Extract the numeric upstream code from an error detail payload."""
    if not isinstance(detail, dict):
        return None
    err = detail.get("error") if isinstance(detail.get("error"), dict) else detail
    for key in ("code", "msg_code"):
        val = err.get(key)
        if isinstance(val, int):
            return val
        if isinstance(val, str) and val.isdigit():
            return int(val)
    msg = str(err.get("msg") or err.get("message") or "")
    m = re.search(r'\bcode["\s:]+(\d{4,5})\b', msg)
    if m:
        return int(m.group(1))
    return None


class _UpstreamGateError(Exception):
    """Upstream refused the request for path-specific reasons (6004/11128).

    Carries the upstream code and the HTTPException to surface when no
    fallback route succeeds.
    """

    def __init__(self, code: int, http_exc: HTTPException):
        super().__init__(f"upstream gate {code}")
        self.code = code
        self.http_exc = http_exc


def _err_event(msg: bytes, status: int) -> bytes:
    chunk = {
        "error": {"message": msg.decode("utf-8", "replace")[:500], "type": "upstream_error", "code": status},
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")


async def _stream_upstream(url: str, headers: dict, body: dict,
                           model_name: str = "?", t0: float = 0.0, rid: str = "",
                           log_body: bool = False):
    """Relay the backend SSE to the client verbatim while parsing for logs.

    The backend already speaks standard OpenAI SSE including tool_calls, so
    bytes pass through unmodified; the parsed events only feed the summary
    log and the full raw-SSE debug capture.
    """
    finish_reason = None
    tool_names: list[str] = []
    usage: dict = {}
    saw_filter = False
    buf = b""
    raw_parts: list[bytes] = []
    prefix = f"[{rid}] " if rid else ""

    def _feed(chunk: bytes):
        nonlocal finish_reason, saw_filter, buf
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            obj = _parse_sse_data(line[5:])
            if obj is None:
                continue
            if obj.get("usage"):
                usage.update(obj["usage"])
            for ch in obj.get("choices") or []:
                if ch.get("finish_reason"):
                    finish_reason = ch["finish_reason"]
                for tc in (ch.get("delta") or {}).get("tool_calls") or []:
                    nm = (tc.get("function") or {}).get("name")
                    if nm:
                        tool_names.append(nm)
            try:
                text_repr = line.decode("utf-8", "replace")
            except Exception:
                text_repr = ""
            if "content-filter" in text_repr or "敏感" in text_repr or "审核" in text_repr:
                saw_filter = True

    try:
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("POST", url, headers=headers, json=body) as r:
                if r.status_code != 200:
                    err = await r.aread()
                    _log(f"{prefix}✗ HTTP {r.status_code} | {model_name} | {_truncate(err.decode('utf-8','replace'),200)}")
                    _log(f"{prefix}── ERROR BODY ──\n{err.decode('utf-8','replace')}")
                    yield _err_event(err, r.status_code)
                    return
                async for chunk in r.aiter_bytes():
                    if chunk:
                        raw_parts.append(chunk)
                        _feed(chunk)
                        yield chunk
    except httpx.HTTPError as e:
        _log(f"{prefix}✗ 网络错误 | {model_name} | {e}")
        yield _err_event(str(e).encode(), 502)

    elapsed = time.time() - t0 if t0 else 0
    tag = " [content-filter]" if (saw_filter or finish_reason == "content-filter") else ""
    _log(f"{prefix}◀ RESPONSE {model_name} | {elapsed:.1f}s | stream finish={finish_reason}{tag}"
         + (f" | tool_calls={tool_names}" if tool_names else "")
         + f" | tokens={usage.get('total_tokens', '?')}")
    if log_body:
        _log(f"{prefix}── RESPONSE RAW SSE ──\n{b''.join(raw_parts).decode('utf-8','replace')}")


async def _post_collect(url: str, headers: dict, body: dict,
                        model_name: str, rid: str) -> dict:
    """POST and aggregate the upstream SSE stream into one chat.completion.

    Raises _UpstreamGateError when upstream answers a path-specific gate
    code from TOKEN_PATH_RETRY_CODES; other non-200s raise HTTPException.
    """
    async with httpx.AsyncClient(timeout=300) as c:
        async with c.stream("POST", url, headers=headers, json=body) as r:
            if r.status_code != 200:
                raw = await r.aread()
                _log(f"[{rid}] ✗ HTTP {r.status_code} | {model_name} | {_truncate(raw.decode('utf-8','replace'),200)}")
                _log(f"[{rid}] ── ERROR BODY ──\n{raw.decode('utf-8','replace')}")
                detail = _safe_err_raw(raw, r.status_code)
                code = _err_code(detail)
                if code in TOKEN_PATH_RETRY_CODES:
                    raise _UpstreamGateError(code, HTTPException(status_code=r.status_code, detail=detail))
                raise HTTPException(status_code=r.status_code, detail=detail)
            return await _collect_stream(r)
