# workbuddy2openai

> 把 **CodeBuddy / WorkBuddy（腾讯代码助手）** 的订阅，转换成 **OpenAI 兼容 API**，让你能在任何支持 OpenAI 协议的客户端（ZCode、Cherry Studio、NextChat、LobeChat 等）里复用它

> ⚠️ **关于 Codex CLI**：新版 Codex CLI 已不再支持 `wire_api = "chat"`，只支持 `completions` 格式，因此**本工具无法直接接入 Codex CLI**，请改用下方「OpenAI 兼容客户端」方案

[English](README.md) · [中文文档](README.zh-CN.md)

---

## 中文文档

一个极简的本地协议转换器（proxy / adapter）：读取你本机已登录的 CodeBuddy 桌面端凭据，把它的对话能力包装成标准的 OpenAI `/v1/chat/completions`、`/v1/models` 接口。**不碰登录授权、不碰你已有的客户端配置、跨平台**

### ✨ 特性

- 🔄 OpenAI 兼容：`/v1/chat/completions`（流式 SSE）、`/v1/models`、`/health`
- 🛠️ Function calling 开箱即用：后端原生支持 `tools` / `tool_calls`，agent 客户端直接跑工具循环，无需 prompt 注入或文本解析
- 🪶 小而直接：一个包直连后端，不调 CLI、不开子进程
- 🔐 自动续期：读取桌面端登录文件，token 过期前自动刷新并回写
- 🔑 API 密钥模式：WorkBuddy 国际版账号可跳过桌面端登录，无界面部署
- 🖥️ 自动定位 macOS / Windows / Linux 上的登录文件
- 🛡️ 默认只监听 `127.0.0.1`，不设 `--api-key` 就不校验
- ⚡ 流式输出，与原生 OpenAI 流式一致

### 🧠 它是怎么工作的

```
ZCode / Cherry Studio / 任意 OpenAI 客户端
        │  POST /v1/chat/completions  (标准 OpenAI 协议，含 tools)
        ▼
┌────────────────────────────┐
│  workbuddy2openai (本地)    │  ← FastAPI 服务 (127.0.0.1:8787)
│  读 token + 注入鉴权 header │
│  + 透传                    │
└────────────────────────────┘
        │  POST /v2/chat/completions  (带 Authorization/X-User-Id 等头)
        ▼
┌────────────────────────────────┐
│  copilot.tencent.com 后端      │  ← 原生标准 OpenAI 协议
│  (GLM / Kimi / DeepSeek / hy3) │     含原生 tools / tool_calls / SSE 流式
└────────────────────────────────┘
```

转换器直连 CodeBuddy 后端（`copilot.tencent.com/v2/chat/completions`），该后端本身就是**标准 OpenAI chat/completions 协议**。转换器只做两件事：①读取本机登录凭据并注入鉴权 header；②在本地 `/v1/*` 与后端 `/v2/*` 之间透传。因为后端原生支持 `tools` / `tool_calls`，function calling 是模型自带能力，**无需任何 prompt 注入或文本解析**。token 过期时转换器会自动调刷新接口并回写。

> 历史版本曾通过「调 CLI + `<tool_call>` 文本标签解析」实现 function calling，但在嵌套 agent（subagent）场景下，subagent 的输出会夹带标签污染对话。**v2.0 改为直连后端，彻底解决了这个问题**

### 📦 前置条件

1. 已安装并**登录** CodeBuddy / WorkBuddy 桌面端（[腾讯云 CodeBuddy 官网](https://www.codebuddy.ai/)）。转换器会自动在这些位置找登录态：
   - **macOS**：`~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/*.info`
   - **Windows**：`%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\*.info`
   - **Linux**：`~/.local/share/CodeBuddyExtension/Data/Public/auth/*.info`
2. **Python 3.11+**（无需 Node.js，不再依赖 CLI）
3. 安装依赖（一次性）：
   ```bash
   pip install -r requirements.txt
   ```

### 🚀 快速开始

```bash
# 1. 克隆
git clone https://github.com/leonid-dalin/codebuddy2openai.git
cd codebuddy2openai

# 2. 装依赖
pip install -r requirements.txt

# 3. 启动（确保 CodeBuddy 桌面端已登录）
python3 -m workbuddy2openai.converter
```

启动时会做一次预检，打印账号信息和 token 状态

用 curl 验证：

```bash
# 列模型
curl http://127.0.0.1:8787/v1/models

# 非流式
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"hy3","messages":[{"role":"user","content":"你好"}]}'

# 流式
curl -N http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"hy3","stream":true,"messages":[{"role":"user","content":"数1到5"}]}'
```

也可以用启动脚本，它会加载 `.env`、选择项目虚拟环境、缺依赖时自动安装：

```bash
./scripts/start.sh          # Linux 和 macOS
scripts\start.bat           # Windows
```

开机自启用 `./scripts/watcher.sh`，`/health` 不通时自动重启代理。systemd 单元、Windows 计划任务和环境变量覆盖见 [scripts/README.md](scripts/README.md)

### 🔑 API 密钥模式（`--direct-key`）

WorkBuddy 国际版账号（`www.workbuddy.ai` 的 Keycloak 域）走桌面端令牌会 401：令牌格式与转换器调用的后端不匹配，刷新也会报 `invalid_grant`。如果你的账号是这种情况，可以完全跳过桌面端登录，改用 **CK_\* API 密钥**（在 [codebuddy.ai/profile/keys](https://www.codebuddy.ai/profile/keys) 生成，即 CLI 文档里的 `CODEBUDDY_API_KEY`）：

```bash
python3 -m workbuddy2openai.converter --direct-key ck_你的密钥
# 或：export CODEBUDDY_DIRECT_KEY=ck_你的密钥
```

该模式下：

- 无需桌面端登录；密钥以 `Authorization: Bearer` 直接发往 `https://www.codebuddy.ai/v2/chat/completions`。可无界面部署（服务器、容器、NAS）
- `/v1/models` 返回国际版目录（2026-08-31 实测）：`auto`、`hy3`、`hy4-preview`、`glm-5.3/5.2/5.1/5v-turbo`、`minimax-m3`、`kimi-k3/k2.7/k2.6`、`deepseek-v4-pro/flash/4.1-flash`、`gpt-5.6-luna/terra/sol`、`gemini-3.1-pro`
- 启动预检（面向桌面端会话）自动跳过

后端的几个坑（先知道，免得对着报错发懵）：

- 请求必须 `stream: true`，非流式会被拒（错误码 `11101`）。转换器对上游永远走流式、再按客户端要求聚合，所以只有自己裸调接口才会碰到
- 第一条消息必须是 `system` 角色（错误码 `11128`）。转换器会在客户端没给时自动补一条
- 模型 ID **区分大小写**，未知的直接拒（错误码 `11102`）：`hy3` 可以，`Hy3` 不行
- `gpt-5.6-luna` 等模型对过小的 `max_tokens` 会拒绝（错误码 `11133`，integer_below_min_value），建议 ≥100

密钥是有效凭证，按密码对待，到期记得轮换

### 🛠️ Function Calling（工具调用）

后端原生支持标准 OpenAI function calling。客户端在请求里带 `tools`，模型原生返回 `tool_calls`（`finish_reason:"tool_calls"`），客户端执行工具后把 `role:"tool"` 的结果回传即可，和直连 OpenAI 完全一致。流式、非流式、多轮工具调用都支持

### 🪶 内容审核脱敏（`--mask`）

后端在推理之前跑关键词内容审核，而部分客户端注入的固定合规模板（「拒绝 DoS 攻击、exploit 开发……」这类声明）会误触关键词。`--mask` 对 system 消息里的这类词插入零宽空格，关键词匹配失效，人和模型读起来无差别

脱敏只针对客户端固定模板，不能也不应绕过对用户真实有害输入的审核

### 🔧 命令行参数

```
python3 -m workbuddy2openai.converter [--host HOST] [--port PORT] [--api-key KEY]
                                      [--direct-key CK_KEY] [--log PATH] [--log-body]
                                      [--mask] [--skip-check]
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--host` | `127.0.0.1` | 监听地址 |
| `--port` | `8787` | 监听端口 |
| `--api-key` | 关 | 启用鉴权；客户端需带同样 key（也可用环境变量 `WORKBUDDY2OPENAI_KEY`）|
| `--direct-key` | 关 | CK_\* 密钥（或环境变量 `CODEBUDDY_DIRECT_KEY`）；设置后进入 API 密钥模式，详见上文 |
| `--log` | 关 | 日志写到该路径（如 `--log converter.log`），不传则不记。也可用环境变量 `WORKBUDDY2OPENAI_LOG` |
| `--log-body` | 关 | 额外记录完整请求/响应体和原始 SSE；需配合 `--log`，会记录全部对话，调试之外别开 |
| `--mask` | 关 | system 消息脱敏，见上文 |
| `--skip-check` | 否 | 跳过启动预检 |

日志按请求 ID 串起来，记录模型、耗时、finish_reason、工具调用、token 数；`--log-body` 时完整落盘请求体和响应报文，排查问题直接按 ID 查即可。示例：

```
[2026-06-19 11:56:32] [9cc4488e] ▶ REQUEST hy3 | stream=False | msgs=1 | last_user='Reply: pong'
[2026-06-19 11:56:35] [9cc4488e] ◀ RESPONSE hy3 | 3.0s | finish=stop | tokens=11
```

### 🤖 可用模型

桌面端模式（中国区）：`glm-5.2`、`glm-5.1`、`glm-5v-turbo`、`kimi-k2.7`、`kimi-k2.6`、`kimi-k2.5`、`deepseek-v4-pro`、`deepseek-v4-flash`、`minimax-m3-pay`、`hy3-preview-agent`、`auto`

API 密钥模式（国际版，2026-08-31 实测）：`auto`、`hy3`、`hy4-preview`、`glm-5.3`、`glm-5.2`、`glm-5.1`、`glm-5v-turbo`、`minimax-m3`、`kimi-k3`、`kimi-k2.7`、`kimi-k2.6`、`deepseek-v4-pro`、`deepseek-v4-flash`、`deepseek-v4.1-flash`、`gpt-5.6-luna`、`gpt-5.6-terra`、`gpt-5.6-sol`、`gemini-3.1-pro`

具体可用性以你的订阅为准；模型 ID 区分大小写

### 📁 项目结构

```
codebuddy2openai/
├── src/workbuddy2openai/
│   ├── converter.py     # 入口（python -m workbuddy2openai.converter）
│   ├── app.py           # FastAPI 端点、客户端鉴权、回退重试
│   ├── credentials.py   # 凭据发现、token 刷新、header 构造
│   ├── upstream.py      # SSE 解析、聚合、Route
│   ├── protocol.py      # 后端协议契约（路径、header、网关码）
│   └── masking.py       # 脱敏模块（可选，--mask 启用）
├── scripts/             # 启动脚本、看护进程、systemd 单元（见 scripts/README.md）
├── tests/               # pytest 测试套件
└── pyproject.toml
```

逆向出来的后端契约集中在 `src/workbuddy2openai/protocol.py` 一个文件里，桌面端更新导致请求失败时先查那里

### 运行测试

```bash
pip install -r requirements-dev.txt
pytest
```

CI 在 Python 3.11、3.12、3.13 上跑测试套件和 pyflakes

### ❓ 常见问题

- **找不到登录文件**：在桌面端完成登录（不是只装、要登进去），路径见「前置条件」。**国际版（workbuddy.ai）账号例外**：桌面端令牌与后端不匹配，登录后仍会 401，请改用 API 密钥模式（`--direct-key`）
- **客户端报 401**：转换器若用了 `--api-key`，客户端那边要带同样的 key；若是后端 401，可能是 token 失效（转换器会自动刷新，若仍失败需在桌面端重新登录）
- **响应慢**：可换 `deepseek-v4-flash` 等更快的模型
- **"敏感内容"被拦截**：这是 CodeBuddy 后端的**内容审核**（腾讯合规策略），在模型推理之前就拦了，常见触发原因是客户端合规模板里的安全相关英文术语。用 `--log` 在日志里看 `content-filter` 标记定位请求，`--mask` 可降低误拦概率

### ⚠️ 免责声明

本项目为个人学习与研究用途，非官方产品，与腾讯 / CodeBuddy / OpenAI 无任何关联。使用本工具即表示你已阅读并同意：仅在你拥有合法订阅的前提下使用，遵守相关服务条款，自负风险

### 📄 开源协议

[GPL-3.0-or-later](./LICENSE)

---

<!-- SEO keywords -->
<sub>
**关键词 / Keywords:** codebuddy to openai · codebuddy2openai · codebuddy openai compatible api · codebuddy api proxy · codebuddy workbuddy openai adapter · tencent codebuddy openai · codebuddy glm-5.2 api · codebuddy kimi deepseek openai · openai compatible proxy local llm gateway · codebuddy function calling · codebuddy tool use tool_calls · codebuddy zcode cherry studio · 腾讯代码助手 openai · codebuddy 转 openai · codebuddy 接入 zcode cherry studio · 本地大模型代理 openai 协议 · codebuddy 订阅 复用 · workbuddy api 转换 · codebuddy 工具调用
</sub>
