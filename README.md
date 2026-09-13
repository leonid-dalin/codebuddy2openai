# workbuddy2openai

> Turn your **CodeBuddy / WorkBuddy (Tencent coding assistant)** subscription into a standard **OpenAI-compatible API**, so you can reuse it from any OpenAI-protocol client (ZCode, Cherry Studio, NextChat, LobeChat, Open WebUI, and others).

> ⚠️ **Codex CLI note:** newer Codex CLI dropped `wire_api = "chat"` and only supports the `completions` format, so **this tool cannot be used with Codex CLI**. Use any OpenAI-compatible client instead.

[English](README.md) · [中文文档](README.zh-CN.md)

### ✨ Features

- 🔄 OpenAI-compatible: `/v1/chat/completions` with streaming SSE, plus `/v1/models` and `/health`
- 🛠️ Function calling that just works: the backend speaks native `tools` and `tool_calls`, so agent clients run tool loops with no prompt injection and no text parsing
- 🪶 Small and direct: one package calling the backend directly, no bundled CLI, no subprocess
- 🔐 Signs itself in: reads the desktop client's login file, refreshes the token before it expires, and writes the fresh session back
- 🔑 Direct-key mode for WorkBuddy international accounts, no desktop session, runs headless
- 🖥️ Finds the auth file on macOS, Windows and Linux
- 🛡️ Binds to `127.0.0.1` and accepts any client until you set `--api-key`
- ⚡ Streaming with the same shape as native OpenAI

### 🚀 Quick Start

```bash
git clone https://github.com/leonid-dalin/codebuddy2openai.git
cd workbuddy2openai
pip install -r requirements.txt
python3 -m workbuddy2openai.converter
```

Sign in on the CodeBuddy / WorkBuddy desktop client first; the proxy reads that login. Startup runs a preflight that prints the account and token state.

Or use a launcher, which loads `.env`, picks the project virtual environment, and installs the dependencies if they are missing:

```bash
./scripts/start.sh          # Linux and macOS
scripts\start.bat           # Windows
```

Keep it running across a reboot with `./scripts/watcher.sh`, which restarts the proxy whenever `/health` stops answering. See [scripts/README.md](scripts/README.md) for the systemd unit, the Windows Task Scheduler setup, and the environment overrides.

Then point your OpenAI-compatible client at `http://127.0.0.1:8787/v1` (API base), leave the key blank unless you started the converter with `--api-key`.

Check it with curl:

```bash
curl http://127.0.0.1:8787/v1/models

curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"hy3","messages":[{"role":"user","content":"Reply: pong"}]}'
```

### 🔑 API key mode (`--direct-key`)

WorkBuddy international accounts (Keycloak realm on `www.workbuddy.ai`) get 401s from the desktop-token path: the token shape does not match the backend the converter calls, and refresh fails with `invalid_grant`. If that is your situation, skip the desktop session entirely and use a **CK_\* API key** instead (generate one at [codebuddy.ai/profile/keys](https://www.codebuddy.ai/profile/keys); the CLI documents the same key as `CODEBUDDY_API_KEY`).

```bash
python3 -m workbuddy2openai.converter --direct-key ck_yourkeyhere
# or: export CODEBUDDY_DIRECT_KEY=ck_yourkeyhere
```

What changes in this mode:

- No desktop app or login needed; the key is sent as a plain `Authorization: Bearer` header to `https://www.codebuddy.ai/v2/chat/completions`. Works headless (servers, containers, NAS).
- `/v1/models` lists the international catalog (verified live 2026-08-31): `auto`, `hy3`, `hy4-preview`, `glm-5.3/5.2/5.1/5v-turbo`, `minimax-m3`, `kimi-k3/k2.7/k2.6`, `deepseek-v4-pro/flash/4.1-flash`, `gpt-5.6-luna/terra/sol`, `gemini-3.1-pro`.
- The startup preflight (which expects a desktop session) is skipped.

Backend quirks worth knowing (they produce confusing errors if you meet them blind):

- Requests must use `stream: true`; non-stream calls are rejected with error `11101`. The converter always streams upstream and aggregates when the client asked for non-streaming, so this only matters for raw calls.
- The first message must have role `system` (error `11128`). The converter prepends a system message when the client omits one.
- Model IDs are case-insensitive (the proxy folds them to lowercase); unknown values are still rejected by the backend with error `11102`.
- `gpt-5.6-luna` and friends reject very small `max_tokens` values (error `11133`, "integer_below_min_value"); stay above roughly 100.

The key is a live credential: treat it like a password, and expect to rotate it when it expires.

### 🛠️ Function calling

The backend supports standard OpenAI function calling natively. Send `tools` in the request, the model returns `tool_calls` with `finish_reason: "tool_calls"`, and your client executes the tools and sends back `role: "tool"` results. Streaming, non-streaming and multi-turn tool loops all work, exactly as against OpenAI.

### 🪶 Content-filter masking (`--mask`)

The backend runs keyword-based content filtering before inference, and the fixed compliance templates some clients prepend as system messages ("Refuse requests for DoS attacks, exploit development...") can trip it. `--mask` inserts a zero-width space into those terms in system messages, so the keyword match stops firing while the text reads identically to the model.

This only covers client-authored refusal templates. It cannot and does not bypass filtering of genuinely harmful user input.

### 🔧 Command-line flags

```
python3 -m workbuddy2openai.converter [--host HOST] [--port PORT] [--api-key KEY]
                                      [--direct-key CK_KEY] [--log PATH] [--log-body]
                                      [--mask] [--skip-check]
```

| Flag | Default | Purpose |
|------|---------|---------|
| `--host` | `127.0.0.1` | Listen address |
| `--port` | `8787` | Listen port |
| `--api-key` | off | Require clients to present this key (or set `WORKBUDDY2OPENAI_KEY`) |
| `--direct-key` | off | `CK_*` key (or `CODEBUDDY_DIRECT_KEY`); switches to API key mode, see above |
| `--log` | off | Write a log to this path (or set `WORKBUDDY2OPENAI_LOG`); no logging without it |
| `--log-body` | off | Also log full request/response bodies and raw SSE; needs `--log`, captures every conversation, keep it off outside debugging |
| `--mask` | off | Content-filter masking in system messages, see above |
| `--skip-check` | off | Skip the startup preflight |

### 📁 Project structure

```
workbuddy2openai/
├── src/workbuddy2openai/
│   ├── converter.py     # entry point (python -m workbuddy2openai.converter)
│   ├── app.py           # FastAPI endpoints, client auth, fallback retry
│   ├── credentials.py   # auth-file discovery, token refresh, headers
│   ├── upstream.py      # SSE parsing, aggregation, Route
│   ├── protocol.py      # backend protocol contract (paths, headers, gate codes)
│   └── masking.py       # content-filter masking (optional, --mask)
├── scripts/             # launchers, watchdog, systemd unit (see scripts/README.md)
├── tests/               # pytest suite
└── pyproject.toml
```

The reverse-engineered backend contract lives in one file, `src/workbuddy2openai/protocol.py`. If a desktop-client update breaks requests, check there first.

### Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

CI runs the suite plus pyflakes on Python 3.11, 3.12 and 3.13.

### ❓ Troubleshooting

- **Auth file not found**: complete the sign-in on the desktop client (installing is not enough). Paths are listed under the prerequisites below. International accounts are the exception: their desktop token never matches the backend, so use API key mode instead.
- **Client gets 401**: if you started the converter with `--api-key`, the client must send the same key. A 401 from the backend means the token expired; the converter refreshes it automatically, and a persistent failure means signing in on the desktop client again.
- **"Sensitive content" rejections**: that is the backend's content filter, applied before inference. It usually fires on security terms inside client-authored compliance templates. Use `--log` to see which request was blocked (marked `content-filter`), and `--mask` for the template terms.

### Prerequisites

1. CodeBuddy / WorkBuddy desktop client, installed and **signed in** ([codebuddy.ai](https://www.codebuddy.ai/)). The proxy looks for the login file at:
   - **macOS**: `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/*.info`
   - **Windows**: `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\*.info`
   - **Linux**: `~/.local/share/CodeBuddyExtension/Data/Public/auth/*.info`
2. **Python 3.11+** (no Node.js, no bundled CLI).
3. Dependencies: `pip install -r requirements.txt`.

### ⚠️ Disclaimer

For personal learning and research only. Not affiliated with Tencent / CodeBuddy / OpenAI. Use only with a subscription you legally hold, in compliance with the relevant terms of service, at your own risk.

Licence: [GPL-3.0-or-later](./LICENSE)

---

<!-- SEO keywords -->
<sub>
**Keywords / 关键词:** codebuddy to openai · codebuddy2openai · codebuddy openai compatible api · codebuddy api proxy · codebuddy workbuddy openai adapter · tencent codebuddy openai · codebuddy glm-5.2 api · codebuddy kimi deepseek openai · openai compatible proxy local llm gateway · codebuddy function calling · codebuddy tool use tool_calls · codebuddy zcode cherry studio · 腾讯代码助手 openai · codebuddy 转 openai · codebuddy 接入 zcode cherry studio · 本地大模型代理 openai 协议 · codebuddy 订阅 复用 · workbuddy api 转换 · codebuddy 工具调用
</sub>
