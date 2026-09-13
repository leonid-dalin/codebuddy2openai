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

import uvicorn

from credentials import (
    BACKEND,
    USER_AGENT,
    CredentialManager,
    auth_dirs,
    backend_for_domain,
    find_auth_file,
)
from upstream import (
    CN_MODELS,
    DEFAULT_MODELS,
    DIRECT_KEY_BACKEND,
    INTL_MODELS,
    PASSTHROUGH_BODY_KEYS,
    TOKEN_PATH_RETRY_CODES,
    _UpstreamGateError,
    _collect_stream,
    _err_code,
    _post_collect,
    _safe_err_raw,
    _stream_upstream,
    _truncate,
)

try:
    from desensitize import desensitize_body
except ImportError:
    def desensitize_body(body, roles=("system",)):
        return body

app = FastAPI(title="codebuddy2openai", version="2.0")
CONFIG: dict = {"api_key": "", "cred": None, "log_path": None,
                "desensitize": False,
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


import upstream as _upstream_module
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
        raise HTTPException(status_code=503, detail={"error": {"message": "未找到登录凭据，请先在桌面端登录 CodeBuddy/WorkBuddy", "type": "auth_error"}})
    return CONFIG["cred"]


def route_for_request() -> tuple[dict, str]:
    """Headers and backend URL base for the configured request mode."""
    if CONFIG.get("direct_key"):
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "Authorization": f"Bearer {CONFIG['direct_key']}",
                   "User-Agent": USER_AGENT}
        return headers, DIRECT_KEY_BACKEND
    headers = _cred().get_headers()
    return headers, backend_for_domain((CONFIG["cred"]._session().get("auth") or {}).get("domain"))


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


def _token_fallback_route() -> tuple[dict, str]:
    cred = CONFIG["cred"]
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": USER_AGENT}
    headers.update(cred.get_headers())
    domain = (cred._session().get("auth") or {}).get("domain")
    return headers, backend_for_domain(domain)


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
        tag = " ⚠️内容审核拦截"
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
                  "auth_file": str(find_auth_file() or "(未找到)"), "mode": "direct-proxy (native function calling)"}
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
    cred = None
    if not CONFIG.get("direct_key"):
        cred = _cred()

    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": {"message": f"bad json: {e}", "type": "invalid_request_error"}})

    messages = payload.get("messages") or []
    if not messages:
        raise HTTPException(status_code=400, detail={"error": {"message": "messages is required", "type": "invalid_request_error"}})

    client_wants_stream = bool(payload.get("stream"))
    body = {k: payload[k] for k in PASSTHROUGH_BODY_KEYS if k in payload}
    body.setdefault("model", "auto")
    body["stream"] = True
    if "stream_options" not in body:
        body["stream_options"] = {"include_usage": True}

    if CONFIG.get("desensitize"):
        body = desensitize_body(body, roles=("system",))

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

    headers, backend = route_for_request()
    if messages and messages[0].get("role") != "system":
        body["messages"] = [{"role": "system", "content": "You are a helpful assistant."}] + body.get("messages", [])
    url = f"{backend}/v2/chat/completions"
    t0 = time.time()

    if client_wants_stream:
        return StreamingResponse(
            _stream_upstream(url, headers, body, model_name, t0, rid,
                             log_body=bool(CONFIG.get("log_body"))),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    collected = await complete_with_fallback(url, headers, body, model_name, rid)
    _log_finish(model_name, t0, collected, rid)
    return JSONResponse(content=collected)


async def complete_with_fallback(url: str, headers: dict, body: dict,
                                 model_name: str, rid: str) -> dict:
    """Non-streaming completion with one gate-code retry over the desktop-token path."""
    try:
        return await _post_collect(url, headers, body, model_name, rid)
    except _UpstreamGateError as gate:
        if not _token_fallback_ready():
            _log(f"[{rid}] ✗ {gate.code} and no desktop token path - surfacing")
            raise gate.http_exc
        fb_headers, fb_backend = _token_fallback_route()
        fb_url = f"{fb_backend}/v2/chat/completions"
        _log(f"[{rid}] ↻ {gate.code} on key path - retrying over desktop token ({fb_backend})")
        try:
            collected = await _post_collect(fb_url, fb_headers, body, model_name, rid)
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
    sys.stderr.write("==== 预检 ====\n")
    sys.stderr.write(f"平台      : {sys.platform}\n")
    sys.stderr.write(f"Python    : {sys.version.split()[0]}\n")
    sys.stderr.write(f"后端      : {BACKEND} (直连，原生 function calling)\n")
    sys.stderr.write(f"登录文件  : {af or '(未找到)'}\n")
    if auth_dirs():
        sys.stderr.write(f"已查目录  : {', '.join(str(d) for d in auth_dirs())}\n")
    ok = True
    if af is None:
        sys.stderr.write("\n[警告] 未找到登录文件。请在桌面端完成登录（CodeBuddy/WorkBuddy）。\n")
        ok = False
    else:
        try:
            cm = CredentialManager(af)
            info = cm.summary()
            sys.stderr.write(f"账号      : {info.get('nickname')} / {info.get('enterpriseName')}\n")
            sys.stderr.write(f"token过期 : {'是(将自动刷新)' if info['token_expired'] else '否'}\n")
        except Exception as e:
            sys.stderr.write(f"[警告] 读取凭据失败：{e}\n")
            ok = False
    sys.stderr.write("================\n")
    return ok
