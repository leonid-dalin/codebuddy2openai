# AGENTS.md

Notes for coding agents (Codex, Claude Code, Hermes, and friends) working in this repository. Humans can read it too, but it is written for a machine that just cloned the repo and wants to be useful fast.

## What this is

A local OpenAI-compatible proxy for the WorkBuddy / CodeBuddy (Tencent) subscription. FastAPI app, src layout, Python 3.11+. It reads the desktop client's auth file, calls the backend directly, and translates nothing except paths and auth headers.

## Install and run

```bash
git clone https://github.com/leonid-dalin/workbuddy2openai.git
cd workbuddy2openai
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]" -r requirements-dev.txt
python -m workbuddy2openai.converter --help             # smoke: entry point resolves
```

Running the real proxy needs a signed-in desktop client (or `--direct-key` with a CK_* key), so in a sandbox verify with the test suite rather than a live request.

## Verify before you claim done

```bash
python -m pytest -q          # 87 tests, no live backend needed (httpx is mocked)
python -m pyflakes src tests # must be silent; CI runs it
```

Both green is the bar. There is no other gate.

## Layout

```
src/workbuddy2openai/
├── converter.py     # argparse entry point; wires flags into CONFIG, starts uvicorn
├── app.py           # FastAPI endpoints, client auth, gate-code fallback retry
├── credentials.py   # auth-file discovery, token refresh, header construction
├── upstream.py      # SSE parsing, stream aggregation, Route dataclass
├── protocol.py      # the reverse-engineered backend contract, one file on purpose
└── masking.py       # optional --mask zero-width-space term masking
tests/               # pytest; conftest exposes canonical modules via fixtures
scripts/             # launchers + watchdog; docs in scripts/README.md
docs/                # Diátaxis tree; READMEs are the landing pages
```

## Rules of this repo

- the backend contract lives only in `protocol.py`; do not scatter literals for paths, header names or gate codes into other modules
- the app layer must not be imported by `credentials` or `upstream`; `tests/test_import_surface.py` enforces the direction
- `converter.py` is an entry point, not a facade: it imports only what `main()` uses, and nothing may rely on re-exports through it
- tests read each symbol from the module that owns it (`workbuddy2openai.app.CONFIG`, not a converter attribute)
- no comments beyond what the code cannot say; explanations go in the commit message
- commit format: Conventional Commits, imperative subject, no trailing period; do not push or merge without the user's say-so
- CI (GitHub Actions, Python 3.11-3.13) runs pytest and pyflakes and must pass before merge

## Known sharp edges

- model IDs fold to lowercase before send (the catalogs are all lowercase; a test guards that) and unknown IDs are rejected by the backend with 11102
- the backend rejects non-streaming calls (error 11101) and requires a system message first (11128); the proxy absorbs both, raw calls do not
- gate codes 6004/11128 mean "retry over the desktop-token path"; see `app.py::complete_with_fallback`
- `EXPIRY_MARGIN_MS` in protocol.py is an observed-latency tunable, not a measured constant
- `docs/` and both READMEs must agree with the code; if you change flags, paths or model lists, update them in the same change
