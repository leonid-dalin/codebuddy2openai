#!/usr/bin/env python3
"""
workbuddy2openai — 把 CodeBuddy / WorkBuddy 的订阅暴露成标准 OpenAI 兼容 API。

原理（直连后端，原生 function calling）：
  - 读取本机已登录的 CodeBuddy 桌面端凭据（auth 文件里的 token / uid / enterpriseId）。
  - 直接转发到 CodeBuddy 后端 `https://copilot.tencent.com/v2/chat/completions`。
    该后端本身就是标准 OpenAI chat/completions 协议（含原生 tools / tool_calls / SSE 流式）。
  - 转换器只做两件事：①注入鉴权 header（Authorization / X-User-Id 等）
    ②在本地 /v1/* 与后端 /v2/* 之间做路径映射与透传。
  - token 过期时自动调 `/v2/plugin/auth/token/refresh` 刷新，并回写 auth 文件。

跨平台：自动定位 auth 目录（macOS / Windows / Linux）。
依赖：fastapi + uvicorn + httpx（pip install fastapi "uvicorn[standard]" httpx）。

用法：
  python3 converter.py                       # 默认 127.0.0.1:8787
  python3 converter.py --port 9000
  python3 converter.py --api-key mysecret    # 启用客户端鉴权
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
    ap = argparse.ArgumentParser(description="CodeBuddy -> OpenAI 兼容转换器（直连后端）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--api-key", default=_env_first("WORKBUDDY2OPENAI_KEY", "CODEBUDDY2OPENAI_KEY"),
                    help="可选：要求客户端携带的 API key（默认不校验）")
    ap.add_argument("--log", default=None, metavar="PATH",
                    help="开启日志并写到该文件（如 --log converter.log 或 --log /tmp/cb.log）。"
                         "不传则不记日志。")
    ap.add_argument("--desensitize", action="store_true",
                    help="启用脱敏：对 system 消息里的合规模板敏感词（DoS/exploit/credential 等）"
                         "插入零宽空格，缓解被后端内容审核误拦。默认关闭。")
    ap.add_argument("--log-body", action="store_true",
                    help="记录完整请求/响应体与原始 SSE 到日志（--log 开启时生效）。默认关闭。")
    ap.add_argument("--direct-key", default=_env_first("WORKBUDDY_DIRECT_KEY", "CODEBUDDY_DIRECT_KEY"),
                    help="CK_* CodeBuddy API key: bypass desktop session, call the international backend directly")
    ap.add_argument("--skip-check", action="store_true", help="跳过启动预检")
    args = ap.parse_args()

    CONFIG["api_key"] = args.api_key
    CONFIG["desensitize"] = args.desensitize
    CONFIG["direct_key"] = (args.direct_key or "").strip() or None
    CONFIG["log_path"] = args.log if args.log else (_env_first("WORKBUDDY2OPENAI_LOG", "CODEBUDDY2OPENAI_LOG") or None)
    CONFIG["log_body"] = args.log_body
    af = find_auth_file()
    CONFIG["cred"] = CredentialManager(af) if (af and not CONFIG["direct_key"]) else None

    if not args.skip_check and not CONFIG["direct_key"]:
        preflight()

    sys.stderr.write(f"\n✅ 监听 http://{args.host}:{args.port}（直连后端，原生 function calling）\n")
    sys.stderr.write("   GET  /v1/models\n")
    sys.stderr.write("   POST /v1/chat/completions   (原生 tools/tool_calls，支持流式)\n")
    sys.stderr.write("   GET  /health\n")
    if args.api_key:
        sys.stderr.write("   鉴权已启用（API key 已设置）\n")
    if CONFIG["log_path"]:
        sys.stderr.write(f"   日志      : {CONFIG['log_path']}\n")
    if args.desensitize:
        sys.stderr.write("   脱敏      : 已启用（system 合规词零宽处理）\n")
    sys.stderr.write("按 Ctrl+C 退出。\n\n")

    from app import _log
    _log("==== converter 启动 ====")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
