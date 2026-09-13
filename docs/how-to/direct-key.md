# How-to: run the proxy headless with a direct key

For WorkBuddy international accounts, and for servers, containers or NAS boxes with no desktop client. The proxy authenticates with a `CK_*` API key instead of the desktop login.

## 1. Generate a key

Create one at [codebuddy.ai/profile/keys](https://www.codebuddy.ai/profile/keys). It starts with `CK_`. Treat it like a password: it is a live credential.

## 2. Start with --direct-key

```bash
python3 -m workbuddy2openai.converter --direct-key ck_yourkeyhere
```

Or keep the key out of the process list:

```bash
export CODEBUDDY_DIRECT_KEY=ck_yourkeyhere
python3 -m workbuddy2openai.converter
```

The startup preflight, which expects a desktop session, is skipped automatically in this mode. `/v1/models` now lists the international catalog.

## 3. Verify

```bash
curl http://127.0.0.1:8787/v1/models
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"hy3","messages":[{"role":"user","content":"Reply: pong"}]}'
```

## Notes for unattended hosts

- bind to localhost only unless something outside the box needs to call the proxy; if it must be reachable, set `--api-key` so the port is not open
- put the key in `.env` rather than the command line; the [launchers](keep-it-running.md) load it with `set -a`
- the key expires; rotate it when requests start failing with auth errors
- see [the gate-code explanation](../explanation/fallback.md) for what happens when the international endpoint refuses a request the desktop path would accept
