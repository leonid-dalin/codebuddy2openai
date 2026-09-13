"""Shared fixtures. Import the package once per session and isolate CONFIG between tests."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture(scope="session")
def converter_module():
    """A test facade over the canonical modules: CONFIG and app live in
    workbuddy2openai.app, model catalogs and helpers in upstream, auth in
    credentials. Tests read them from the module that owns each symbol."""
    import httpx

    import workbuddy2openai.app as app_module
    import workbuddy2openai.credentials as credentials
    import workbuddy2openai.upstream as upstream

    return SimpleNamespace(
        CONFIG=app_module.CONFIG,
        app=app_module.app,
        _check_auth=app_module._check_auth,
        _cred=app_module._cred,
        CredentialManager=credentials.CredentialManager,
        backend_for_domain=credentials.backend_for_domain,
        CN_MODELS=upstream.CN_MODELS,
        INTL_MODELS=upstream.INTL_MODELS,
        DEFAULT_MODELS=upstream.DEFAULT_MODELS,
        _err_code=upstream._err_code,
        httpx=httpx,
    )


@pytest.fixture(scope="session")
def credentials_module(converter_module):
    """The credentials module the converter delegates auth discovery to.

    Tests that patch auth_dirs must patch it here: production reads
    workbuddy2openai.credentials.auth_dirs, so a patch on the converter
    facade is invisible.
    """
    import workbuddy2openai.credentials as credentials
    return credentials


@pytest.fixture()
def fresh_config(converter_module):
    """Reset CONFIG to defaults so tests cannot leak state into each other."""
    converter_module.CONFIG["api_key"] = ""
    converter_module.CONFIG["mask"] = False
    converter_module.CONFIG["log_path"] = None
    converter_module.CONFIG["direct_key"] = None
    converter_module.CONFIG["cred"] = None
    yield converter_module.CONFIG
    converter_module.CONFIG["api_key"] = ""
    converter_module.CONFIG["mask"] = False
    converter_module.CONFIG["log_path"] = None
    converter_module.CONFIG["direct_key"] = None
    converter_module.CONFIG["cred"] = None


@pytest.fixture()
def client(converter_module, fresh_config):
    """FastAPI TestClient wired to a converter with no credentials."""
    from fastapi.testclient import TestClient

    return TestClient(converter_module.app)


@pytest.fixture()
def authed_client(converter_module, client):
    converter_module.CONFIG["api_key"] = "test-key"
    client.headers.update({"Authorization": "Bearer test-key"})
    return client


@pytest.fixture()
def direct_key_client(authed_client, converter_module):
    converter_module.CONFIG["direct_key"] = "ck_test_dummy"
    return authed_client


@pytest.fixture()
def fake_auth_file(tmp_path):
    """A plausible desktop auth file for the WorkBuddy international realm."""
    auth_dir = tmp_path / "CodeBuddyExtension" / "Data" / "Public" / "auth"
    auth_dir.mkdir(parents=True)
    payload = {
        "account": {
            "uid": "test-uid-1234",
            "nickname": "tester@example.com",
            "enterpriseId": None,
        },
        "auth": {
            "accessToken": "test-access-token",
            "refreshToken": "test-refresh-token",
            "expiresAt": 9999999999999,
            "domain": "www.workbuddy.ai",
        },
    }
    auth_file = auth_dir / "workbuddy-desktop-ai.info"
    auth_file.write_text(__import__("json").dumps(payload), encoding="utf-8")
    return auth_file, payload
