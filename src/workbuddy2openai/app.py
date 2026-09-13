"""FastAPI surface: endpoints, client auth, logging config, fallback retry.

Owns CONFIG as the single runtime configuration object and routes requests
between the direct-key and desktop-token paths, including the one-retry
fallback when the key path meets a path-specific gate code.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from workbuddy2openai import protocol
from workbuddy2openai.credentials import (
    USER_AGENT,
    CredentialManager,
    auth_dirs,
    find_auth_file,
)
from workbuddy2openai.upstream import (
    DEFAULT_MODELS,
    DIRECT_KEY_BACKEND,
    INTL_MODELS,
    PASSTHROUGH_BODY_KEYS,
    Route,
    _UpstreamGateError,
    _post_collect,
    _stream_upstream,
    _truncate,
)

from workbuddy2openai.masking import mask_body

app = FastAPI(title="workbuddy2openai", version="2.0")
CONFIG: dict = {"api_key": "", "cred": None, "log_path": None,
                "mask": False,
                "direct_key": None,
                "log_body": False}

_LOG_LOCK = threading.Lock()


def _log(msg: str):
    path = CONFIG.get("log_path")
    if not path:
        return
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        with _LOG_LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except OSError:
        pass


from workbuddy2openai import upstream as _upstream_module
_upstream_module.set_log_sink(_log)


def _env_first(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def _check_auth(authorization: str, x_api_key: Optional[str]):
    key = CONFIG["api_key"]
    if not key:
        return
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    if not token and x_api_key:
        token = x_api_key
    if token != key:
        raise HTTPException(status_code=401, detail={"error": {"message": "invalid api key", "type": "auth_error"}})


def _cred() -> CredentialManager:
    if CONFIG.get("direct_key"):
        raise HTTPException(status_code=500, detail={"error": {"message": "internal: _cred called in direct-key mode", "type": "auth_error"}})
    if CONFIG["cred"] is None:
        raise HTTPException(status_code=503, detail={"error": {"message": "no login credentials found; sign in on the WorkBuddy desktop client first", "type": "auth_error"}})
    return CONFIG["cred"]


def route_for_request() -> Route:
    """The upstream route for the configured request mode."""
    if CONFIG.get("direct_key"):
        return Route(
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Authorization": f"Bearer {CONFIG['direct_key']}",
                     "User-Agent": USER_AGENT},
            base_url=DIRECT_KEY_BACKEND,
            kind="direct-key",
        )
    cred = _cred()
    return Route(cred.get_headers(), cred.backend_url(), kind="desktop-token")


def _token_fallback_ready() -> bool:
    cred = CONFIG.get("cred")
    if cred is None:
        return False
    try:
        cred.get_headers()
        return True
    except Exception as e:
        _log(f"[fallback] desktop token path unavailable: {e}")
        return False


def _token_fallback_route() -> Route:
    cred = CONFIG["cred"]
    return Route(
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": USER_AGENT, **cred.get_headers()},
        base_url=cred.backend_url(),
        kind="desktop-token",
    )


def _last_user_text(messages: list) -> str:
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        content = m.get("content", "")
        if isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    return str(blk.get("text", ""))
            return ""
        return str(content)
    return ""


def _log_finish(model_name: str, t0: float, result: dict, rid: str = ""):
    elapsed = time.time() - t0
    prefix = f"[{rid}] " if rid else ""
    choice = (result.get("choices") or [{}])[0]
    finish = choice.get("finish_reason")
    msg = choice.get("message") or {}
    tcs = msg.get("tool_calls") or []
    usage = result.get("usage") or {}
    tag = ""
    if finish == "content-filter":
        tag = " [content-filter]"
    tc_names = [t.get("function", {}).get("name") for t in tcs]
    _log(f"{prefix}◀ RESPONSE {model_name} | {elapsed:.1f}s | finish={finish}{tag}"
         + (f" | tool_calls={tc_names}" if tc_names else "")
         + f" | tokens={usage.get('total_tokens', '?')}")
    if CONFIG.get("log_body"):
        _log(f"{prefix}── RESPONSE BODY ──\n{json.dumps(result, ensure_ascii=False, indent=2)}")


@app.get("/health")
def health():
    cred = CONFIG["cred"]
    info: dict = {"status": "ok", "platform": sys.platform, "python": sys.version.split()[0],
                  "auth_file": str(find_auth_file() or "(not found)"), "mode": "direct-proxy (native function calling)"}
    if cred is not None:
        try:
            info["credential"] = cred.summary()
        except Exception as e:
            info["credential_error"] = str(e)
    return info


@app.get("/v1/models")
def list_models(authorization: str = Header(default=None),
                x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key")):
    _check_auth(authorization, x_api_key)
    models = INTL_MODELS if CONFIG.get("direct_key") else DEFAULT_MODELS
    data = [{"id": m, "object": "model", "created": 1700000000, "owned_by": "codebuddy"}
            for m in models]
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request,
                           authorization: str = Header(default=None),
                           x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key")):
    _check_auth(authorization, x_api_key)
    if not CONFIG.get("direct_key"):
        _cred()

    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": {"message": f"bad json: {e}", "type": "invalid_request_error"}})

    messages = payload.get("messages") or []
    if not messages:
        raise HTTPException(status_code=400, detail={"error": {"message": "messages is required", "type": "invalid_request_error"}})

    client_wants_stream = bool(payload.get("stream"))
    body = {k: payload[k] for k in PASSTHROUGH_BODY_KEYS if k in payload}
    body["model"] = str(body.get("model", "auto")).lower()
    body["stream"] = True
    if "stream_options" not in body:
        body["stream_options"] = {"include_usage": True}

    if CONFIG.get("mask"):
        body = mask_body(body, roles=("system",))

    model_name = payload.get("model", "auto")
    tool_names = [t.get("function", {}).get("name") for t in (payload.get("tools") or [])
                  if isinstance(t, dict)]
    last_user = _last_user_text(messages)
    rid = os.urandom(4).hex()
    _log(f"[{rid}] ▶ REQUEST {model_name} | stream={client_wants_stream} | msgs={len(messages)}"
         + (f" | tools={tool_names}" if tool_names else "")
         + (f" | last_user={_truncate(last_user, 60)!r}" if last_user else ""))
    if CONFIG.get("log_body"):
        _log(f"[{rid}] ── REQUEST BODY (发往后端) ──\n{json.dumps(body, ensure_ascii=False, indent=2)}")

    route = route_for_request()
    if messages and messages[0].get("role") != "system":
        body["messages"] = [{"role": "system", "content": "You are a helpful assistant."}] + body.get("messages", [])
    url = route.chat_url()
    t0 = time.time()

    if client_wants_stream:
        return StreamingResponse(
            _stream_upstream(url, route.headers, body, model_name, t0, rid,
                             log_body=bool(CONFIG.get("log_body"))),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    collected = await complete_with_fallback(route, body, model_name, rid)
    _log_finish(model_name, t0, collected, rid)
    return JSONResponse(content=collected)


async def complete_with_fallback(route: Route, body: dict,
                                 model_name: str, rid: str) -> dict:
    """Non-streaming completion with one gate-code retry over the desktop-token path."""
    try:
        return await _post_collect(route.chat_url(), route.headers, body, model_name, rid)
    except _UpstreamGateError as gate:
        if not _token_fallback_ready():
            _log(f"[{rid}] ✗ {gate.code} and no desktop token path - surfacing")
            raise gate.http_exc
        fb_route = _token_fallback_route()
        _log(f"[{rid}] ↻ {gate.code} on key path - retrying over desktop token ({fb_route.base_url})")
        try:
            collected = await _post_collect(fb_route.chat_url(), fb_route.headers, body, model_name, rid)
            _log(f"[{rid}] ✓ desktop-token retry succeeded")
        except _UpstreamGateError as gate2:
            _log(f"[{rid}] ✗ desktop-token retry also gated ({gate2.code}) - surfacing original")
            raise gate.http_exc
        except HTTPException:
            raise
        except httpx.HTTPError as e:
            _log(f"[{rid}] ✗ desktop-token retry network error: {e}")
            raise gate.http_exc
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        _log(f"[{rid}] ✗ 网络错误 | {model_name} | {e}")
        raise HTTPException(status_code=502, detail={"error": {"message": f"upstream error: {e}", "type": "upstream_error"}})
    return collected


def preflight() -> bool:
    af = find_auth_file()
    sys.stderr.write("==== preflight ====\n")
    sys.stderr.write(f"platform   : {sys.platform}\n")
    sys.stderr.write(f"python     : {sys.version.split()[0]}\n")
    sys.stderr.write(f"backend    : {protocol.BACKEND_CN} (direct, native function calling)\n")
    sys.stderr.write(f"login file : {af or '(not found)'}\n")
    if auth_dirs():
        sys.stderr.write(f"searched   : {', '.join(str(d) for d in auth_dirs())}\n")
    ok = True
    if af is None:
        sys.stderr.write("\n[warning] login file not found. Complete the sign-in on the WorkBuddy desktop client.\n")
        ok = False
    else:
        try:
            cm = CredentialManager(af)
            info = cm.summary()
            sys.stderr.write(f"account    : {info.get('nickname')} / {info.get('enterpriseName')}\n")
            sys.stderr.write(f"token expiry: {'expired (will refresh)' if info['token_expired'] else 'valid'}\n")
        except Exception as e:
            sys.stderr.write(f"[warning] failed to read credentials: {e}\n")
            ok = False
    sys.stderr.write("================\n")
    return ok
