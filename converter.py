#!/usr/bin/env python3
"""
workbuddy2openai exposes a locally logged-in WorkBuddy subscription as a standard OpenAI-compatible API.

How it works (direct backend, native function calling):
  - reads the WorkBuddy desktop credentials stored on this machine
    (token / uid / enterpriseId from the auth file).
  - forwards straight to the upstream backend, which already speaks the
    standard OpenAI chat protocol (native tools / tool_calls / SSE streaming).
  - the converter does two things: injects the auth headers, and maps
    local /v1/* paths onto the backend /v2/* paths.
  - refreshes the token automatically before expiry via
    /v2/plugin/auth/token/refresh and writes the auth file back.

Cross-platform: locates the auth directory on macOS / Windows / Linux.
Dependencies: fastapi + uvicorn + httpx (pip install fastapi "uvicorn[standard]" httpx).

Usage:
  python3 converter.py
  python3 converter.py --port 9000
  python3 converter.py --api-key mysecret
"""

from __future__ import annotations

import argparse
import sys

import httpx
import uvicorn

from app import (
    CONFIG,
    _check_auth,
    _cred,
    _env_first,
    app,
    chat_completions,
    complete_with_fallback,
    health,
    list_models,
    preflight,
    route_for_request,
)
from credentials import (
    CredentialManager,
    auth_dirs,
    find_auth_file,
)
from upstream import (
    BACKEND,
    BACKEND_BY_DOMAIN,
    CN_MODELS,
    DEFAULT_MODELS,
    DIRECT_KEY_BACKEND,
    INTL_MODELS,
    PASSTHROUGH_BODY_KEYS,
    TOKEN_PATH_RETRY_CODES,
    USER_AGENT,
    _err_code,
    backend_for_domain,
)


def main():
    ap = argparse.ArgumentParser(description="WorkBuddy to OpenAI-compatible converter (direct backend)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--api-key", default=_env_first("WORKBUDDY2OPENAI_KEY", "CODEBUDDY2OPENAI_KEY"),
                    help="require clients to present this API key (off by default)")
    ap.add_argument("--log", default=None, metavar="PATH",
                    help="write a log to this path; no logging when omitted")
    ap.add_argument("--mask", action="store_true",
                    help="insert zero-width spaces into compliance-template terms in system messages to avoid upstream content-filter false positives (off by default)")
    ap.add_argument("--log-body", action="store_true",
                    help="log full request/response bodies and raw SSE (needs --log; off by default)")
    ap.add_argument("--direct-key", default=_env_first("WORKBUDDY_DIRECT_KEY", "CODEBUDDY_DIRECT_KEY"),
                    help="CK_* WorkBuddy API key: bypass the desktop session and call the international backend directly")
    ap.add_argument("--skip-check", action="store_true", help="skip the startup preflight")
    args = ap.parse_args()

    CONFIG["api_key"] = args.api_key
    CONFIG["mask"] = args.mask
    CONFIG["direct_key"] = (args.direct_key or "").strip() or None
    CONFIG["log_path"] = args.log if args.log else (_env_first("WORKBUDDY2OPENAI_LOG", "CODEBUDDY2OPENAI_LOG") or None)
    CONFIG["log_body"] = args.log_body
    af = find_auth_file()
    CONFIG["cred"] = CredentialManager(af) if (af and not CONFIG["direct_key"]) else None

    if not args.skip_check and not CONFIG["direct_key"]:
        preflight()

    sys.stderr.write(f"\nlistening on http://{args.host}:{args.port} (direct backend, native function calling)\n")
    sys.stderr.write("   GET  /v1/models\n")
    sys.stderr.write("   POST /v1/chat/completions   (native tools/tool_calls, streaming supported)\n")
    sys.stderr.write("   GET  /health\n")
    if args.api_key:
        sys.stderr.write("   auth: enabled (API key set)\n")
    if CONFIG["log_path"]:
        sys.stderr.write(f"   log        : {CONFIG['log_path']}\n")
    if args.mask:
        sys.stderr.write("   mask        : enabled (zero-width handling in system messages)\n")
    sys.stderr.write("press Ctrl+C to exit.\n\n")

    from app import _log
    _log("==== converter started ====")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
