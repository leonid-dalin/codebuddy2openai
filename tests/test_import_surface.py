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
    conv = split_modules["conv"]
    creds = split_modules["creds"]
    upstream = split_modules["upstream"]
    app = split_modules["app"]
    for attr in ("CredentialManager", "auth_dirs", "find_auth_file"):
        assert getattr(conv, attr) is getattr(creds, attr)
    for attr in ("backend_for_domain", "TOKEN_PATH_RETRY_CODES",
                 "CN_MODELS", "INTL_MODELS", "PASSTHROUGH_BODY_KEYS",
                 "USER_AGENT", "BACKEND", "DIRECT_KEY_BACKEND"):
        assert getattr(conv, attr) is getattr(upstream, attr)
    for attr in ("app", "CONFIG", "preflight", "_check_auth", "_cred",
                 "chat_completions", "list_models", "health",
                 "route_for_request", "complete_with_fallback"):
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


def test_masking_fallback_present(split_modules):
    assert callable(split_modules["app"].mask_body)


def test_models_owned_by_value_stable(client):
    response = client.get("/v1/models")
    assert {m["owned_by"] for m in response.json()["data"]} == {"codebuddy"}


def test_env_var_precedence_order(monkeypatch, split_modules):
    monkeypatch.setenv("CODEBUDDY2OPENAI_KEY", "old-name")
    monkeypatch.delenv("WORKBUDDY2OPENAI_KEY", raising=False)
    assert split_modules["app"]._env_first("WORKBUDDY2OPENAI_KEY", "CODEBUDDY2OPENAI_KEY") == "old-name"


def test_user_agent_renamed(split_modules):
    assert split_modules["upstream"].USER_AGENT == "workbuddy2openai/2.0"
