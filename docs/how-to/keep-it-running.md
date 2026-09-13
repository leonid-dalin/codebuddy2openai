# How-to: keep the proxy running unattended

Two tools for this: the launchers start the proxy with sensible environment handling, the watchdog restarts it when it dies. Details and all environment overrides live in [scripts/README.md](../../scripts/README.md); this page is the working set.

## Start once, on boot-safe terms

```bash
./scripts/start.sh              # Linux and macOS
scripts\start.bat               # Windows
```

The launcher loads `.env` from the repository root, picks `.venv` or `venv` if present, installs `requirements.txt` when imports fail, and passes any extra arguments through to the converter:

```bash
./scripts/start.sh --port 9000 --log converter.log
```

## Keep it running

```bash
./scripts/watcher.sh
```

Every 60 seconds the watcher checks `/health`. When it stops answering, the watcher stops the proxy process and starts it again, logging the outcome to `watcher.log`. It only touches a process whose command line contains `workbuddy2openai`, so unrelated Python services on the same host are safe.

A lock file keeps one watcher active; a stale lock from a `SIGKILL`ed watcher is discarded automatically. Ctrl+C stops the watcher and removes the lock.

## Run it as a service

Linux with systemd: install the template unit and enable it.

```bash
sudo cp scripts/workbuddy2openai@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now workbuddy2openai@default
```

Windows: use Task Scheduler to run `scripts\watcher.bat` at logon. Both setups are described step by step in [scripts/README.md](../../scripts/README.md).

## Debug a failing instance

1. start with `--log proxy.log --log-body` (only while debugging; `--log-body` records every conversation)
2. reproduce the failing request
3. find it in the log by its request ID and read the exact body sent and response received
4. if the backend refused the request, check [the protocol explanation](../explanation/protocol.md) for known gate codes
