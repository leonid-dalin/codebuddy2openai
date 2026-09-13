# Tutorial: your first request

From an empty directory to a completed chat request through the proxy. About ten minutes, assuming the WorkBuddy desktop client is installed.

## 1. Sign in on the desktop client

Open the CodeBuddy / WorkBuddy desktop client and sign in. The proxy does not log in for you; it reads the session the client stored:

- macOS: `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/*.info`
- Windows: `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\*.info`
- Linux: `~/.local/share/CodeBuddyExtension/Data/Public/auth/*.info`

If you are on a WorkBuddy international account, stop here and follow [the direct-key guide](../how-to/direct-key.md) instead: the desktop token does not work for that realm.

## 2. Start the proxy

```bash
git clone https://github.com/leonid-dalin/codebuddy2openai.git
cd workbuddy2openai
pip install -r requirements.txt
python3 -m workbuddy2openai.converter
```

The startup preflight prints the account it found and whether the token is live. When you see `listening on http://127.0.0.1:8787`, the proxy is up.

## 3. Ask for the model list

```bash
curl http://127.0.0.1:8787/v1/models
```

You should get a JSON list of model IDs. This is also your first proof the auth plumbing works: the proxy read your login file and constructed the headers the backend expects.

## 4. Send one chat request

```bash
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"hy3","messages":[{"role":"user","content":"Reply: pong"}]}'
```

Expect a normal OpenAI-shaped `chat.completion` back, with `choices[0].message.content` containing roughly `pong`.

## 5. Stream one

```bash
curl -N http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"hy3","stream":true,"messages":[{"role":"user","content":"Count to five"}]}'
```

Same endpoint, but the answer arrives as `data:` lines, one JSON chunk per token batch, ending with `data: [DONE]`. Any OpenAI SDK client does this for you when you set `stream: true`.

## 6. Point a real client at it

In any OpenAI-compatible client (ZCode, Cherry Studio, an OpenAI SDK script):

- API base: `http://127.0.0.1:8787/v1`
- API key: blank, unless you started the proxy with `--api-key`
- Model: one from the list in step 3

## What you built

A local proxy that turns your existing subscription into an OpenAI endpoint. From here:

- keep it running unattended: [the launchers and watchdog guide](../how-to/keep-it-running.md)
- drive tools from an agent client: [function calling](../how-to/function-calling.md)
- understand what happens between your request and the backend: [the explanation section](../explanation/architecture.md)
