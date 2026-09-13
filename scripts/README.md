# Launcher scripts

Scripts that start the proxy and keep it running. The Python package itself needs none of them: `python converter.py` works on its own. These add `.env` loading, virtual environment selection, and automatic restart.

## Files

- `start.sh` starts the proxy after loading `.env` from the repository root.
- `watcher.sh` polls `/health` and restarts the proxy when it stops answering.
- `start.bat` starts the proxy on Windows.
- `start-if-needed.bat` starts it only when the port is not already serving.
- `watcher.bat` launches `watcher.sh` through Git Bash.
- `workbuddy2openai@.service` is a systemd template unit for a Linux host.

## Starting the proxy

Linux and macOS:

```
./scripts/start.sh
./scripts/start.sh --port 9000 --log converter.log
```

Windows:

```
scripts\start.bat
scripts\start.bat --port 9000 --log converter.log
```

Both select the project virtual environment when one exists, in this order: `.venv`, then `venv`. On Windows the lookup covers `Scripts\python.exe`, and on Linux and macOS `bin/python`. When no environment is found the script warns and uses the system interpreter. It then checks that `httpx`, `fastapi` and `uvicorn` import, and installs `requirements.txt` if they do not.

Any argument is passed through to `converter.py`, so the flags in the main README work unchanged.

`.env` is loaded with `set -a`, so every variable in the file reaches the process environment. No secret is placed on the command line; the `.env` file is the safer channel for keys. The proxy binds to `127.0.0.1:8787` by default, and without a key in `.env` or `--api-key` it accepts any client on that port.

`--log` records request and response summaries only. Add `--log-body` alongside it to also write full request and response bodies and the raw upstream SSE stream to the log file. That capture includes every conversation, so keep it off outside debugging.

## Keeping it running

`watcher.sh` checks `/health` every 60 seconds. When the check fails it stops the process holding the port, starts the proxy again, and writes the outcome to `watcher.log`. It only kills a process whose command line contains `converter.py`, so an unrelated Python service on the same host is left alone.

A lock file at `watcher.pid` keeps one watcher active. The lock records a pid, and a second invocation exits when that pid is still a running `watcher.sh`. A stale lock is discarded, so a watcher killed with `SIGKILL` does not block the next one.

On Windows, `watcher.bat` locates Git Bash and runs the same script. A Windows process reached through `netstat` reports a native pid, which Git Bash cannot read from `/proc`, so the script asks PowerShell for that process's command line.

Stop the watcher with `Ctrl+C`. It removes its lock file on `SIGINT` and `SIGTERM`.

### Environment overrides

- `WORKBUDDY2OPENAI_RUNTIME_DIR` where the lock file goes. Defaults to the repository root; `/run/workbuddy2openai` under systemd.
- `WORKBUDDY2OPENAI_LOG` the log path. Defaults to `watcher.log` in the repository root.
- `WORKBUDDY2OPENAI_HEALTH_URL` the check target. Defaults to `http://127.0.0.1:8787/health`.
- `WORKBUDDY2OPENAI_PORT` the port to inspect when stopping a stuck process. Defaults to `8787`.
- `WORKBUDDY2OPENAI_INTERVAL` seconds between checks. Defaults to `60`.

## Running it at boot on Linux

The unit is a template, so the instance name is the user that runs the proxy:

```
sudo cp scripts/workbuddy2openai@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now workbuddy2openai@pi.service
journalctl -u workbuddy2openai@pi.service -f
```

Replace `pi` with your username. The unit expects the checkout at `/home/<user>/codebuddy2openai`; edit `WorkingDirectory` and the two `ExecStart` paths when it lives elsewhere.

`Restart=always` covers the watcher's own exit, and the watcher covers the proxy's. Both write to `watcher.log`.

## Running it at logon on Windows

`start-if-needed.bat` is built for this. Create a Task Scheduler task that runs it at logon, with the start-in directory set to the repository root. The task does nothing when the proxy is already answering, so a duplicate trigger is harmless.

To supervise rather than start once, point the task at `watcher.bat` instead. That needs Git for Windows for `bash.exe`; the script reports the missing dependency and exits when it cannot find it.

## Verifying a change to these scripts

Run `bash -n scripts/*.sh` to catch syntax errors. To exercise the restart path, stop the proxy, run the watcher in one terminal, and start the proxy from another:

```
./scripts/watcher.sh
```

The log should record the stop and the restart within two check intervals.
