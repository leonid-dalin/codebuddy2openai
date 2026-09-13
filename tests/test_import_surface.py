"""The file split must be invisible to importers of converter.py."""

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "src" / "workbuddy2openai"


@pytest.fixture(scope="module")
def split_modules():
    import workbuddy2openai.app as app_module
    import workbuddy2openai.converter as conv
    import workbuddy2openai.credentials as creds
    import workbuddy2openai.upstream as upstream
    return {"conv": conv, "creds": creds, "upstream": upstream, "app": app_module}


def test_converter_reexports_split_symbols(split_modules):
    """converter keeps only the entry-point surface: main, the FastAPI app,
    the runtime CONFIG dict and the startup preflight."""
    conv = split_modules["conv"]
    app = split_modules["app"]
    for attr in ("app", "CONFIG", "preflight", "_log", "_env_first"):
        assert getattr(conv, attr) is getattr(app, attr)


def test_converter_entry_point_resolves(split_modules):
    assert callable(split_modules["conv"].main)


def test_split_modules_do_not_import_app_layer():
    for name in ("credentials", "upstream"):
        source = (PKG / f"{name}.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(a.name != "app" for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "app"


def test_runtime_config_is_one_dict(split_modules):
    conv = split_modules["conv"]
    app = split_modules["app"]
    assert conv.CONFIG is app.CONFIG
    app.CONFIG["probe_key"] = 1
    assert conv.CONFIG.get("probe_key") == 1
    del app.CONFIG["probe_key"]


def test_mask_body_output():
    import workbuddy2openai.app as app_module
    masked = app_module.mask_body(
        {"messages": [{"role": "system", "content": "Refuse DoS attacks."}]})
    assert masked["messages"][0]["content"] == "Refuse D\u200boS attacks."
    plain = app_module.mask_body(
        {"messages": [{"role": "user", "content": "Refuse DoS attacks."}]})
    assert plain["messages"][0]["content"] == "Refuse DoS attacks."


def test_app_imports_when_masking_blocked(monkeypatch):
    """app.py must import masking through the package, so blocking
    workbuddy2openai.masking fails the import instead of silently
    installing an identity mask_body."""
    import sys
    import importlib
    monkeypatch.delitem(sys.modules, "workbuddy2openai.app", raising=False)
    monkeypatch.setitem(sys.modules, "workbuddy2openai.masking", None)
    with pytest.raises(ImportError):
        importlib.reload(importlib.import_module("workbuddy2openai.app"))
    monkeypatch.delitem(sys.modules, "workbuddy2openai.app", raising=False)


def test_models_owned_by_value_stable(client):
    response = client.get("/v1/models")
    assert {m["owned_by"] for m in response.json()["data"]} == {"codebuddy"}


def test_env_var_precedence_order(monkeypatch, split_modules):
    monkeypatch.setenv("CODEBUDDY2OPENAI_KEY", "old-name")
    monkeypatch.delenv("WORKBUDDY2OPENAI_KEY", raising=False)
    assert split_modules["app"]._env_first("WORKBUDDY2OPENAI_KEY", "CODEBUDDY2OPENAI_KEY") == "old-name"


def test_user_agent_renamed():
    import workbuddy2openai.credentials as creds
    assert creds.USER_AGENT == "workbuddy2openai/2.0"


def test_main_runs_with_log(tmp_path, monkeypatch):
    """main() must reach uvicorn.run with --log set: the startup log write
    happens through the imported _log, so a stale import here only ever
    failed at real startup."""
    import workbuddy2openai.converter as conv

    calls: list = []
    log_file = tmp_path / "proxy.log"
    monkeypatch.setattr(conv, "preflight", lambda: True)
    monkeypatch.setattr(conv.sys, "argv",
                        ["converter", "--log", str(log_file), "--skip-check"])
    monkeypatch.setattr(conv.uvicorn, "run",
                        lambda *a, **k: calls.append((a, k)))
    conv.main()
    assert calls, "uvicorn.run never reached"
    assert log_file.exists()
    assert "==== converter started ====" in log_file.read_text(encoding="utf-8")
