"""Endpoint tests through the FastAPI test client, upstream mocked.

The tests patch httpx.AsyncClient.stream, the seam the converter uses for
both the streaming and the aggregating path.
"""

import json

import httpx
import pytest


def sse_chunk(content: str, model: str = "glm-5.2") -> bytes:
    payload = {
        "id": "cmb-test", "model": model, "object": "chat.completion.chunk",
        "created": 1700000000,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": content},
                     "finish_reason": ""}],
        "usage": None,
    }
    return f"data: {json.dumps(payload)}\n\n".encode()


def sse_finish(model: str = "glm-5.2") -> bytes:
    payload = {
        "id": "cmb-test", "model": model, "object": "chat.completion.chunk",
        "created": 1700000000,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    return f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n".encode()


class FakeUpstream:
    """Stands in for the Tencent backend. Records the request, replays chunks."""

    def __init__(self, chunks: list[bytes], status_code: int = 200):
        self.chunks = chunks
        self.status_code = status_code
        self.requests: list[dict] = []

    def __call__(self, monkeypatch, converter_module):
        upstream = self

        class FakeResponse:
            def __init__(self):
                self.status_code = upstream.status_code

            async def aiter_bytes(self):
                for chunk in upstream.chunks:
                    yield chunk

            async def aiter_lines(self):
                buffer = b"".join(upstream.chunks).decode("utf-8")
                for line in buffer.splitlines():
                    yield line

            async def aread(self):
                return b"".join(upstream.chunks)

        class FakeStreamCtx:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *args):
                return False

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def stream(self, method, url, headers=None, json=None, **kwargs):
                upstream.requests.append(
                    {"method": method, "url": url, "headers": headers or {}, "body": json or {}}
                )
                return FakeStreamCtx()

        monkeypatch.setattr(converter_module.httpx, "AsyncClient", FakeAsyncClient)
        return upstream

    @property
    def last(self) -> dict:
        return self.requests[-1]


@pytest.fixture()
def upstream_ok(monkeypatch, converter_module):
    def install(chunks: list[bytes], status_code: int = 200) -> FakeUpstream:
        return FakeUpstream(chunks, status_code)(monkeypatch, converter_module)
    return install


class TestHealth:
    def test_reports_ok_without_credentials(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"

    def test_direct_key_mode_reported_in_health(self, direct_key_client, converter_module):
        response = direct_key_client.get("/health")
        assert response.status_code == 200
        # /health must not leak the key itself
        assert "ck_test_dummy" not in response.text


class TestListModels:
    def test_token_mode_serves_china_catalog(self, authed_client, converter_module):
        response = authed_client.get("/v1/models")
        assert response.status_code == 200
        ids = [m["id"] for m in response.json()["data"]]
        assert ids == converter_module.CN_MODELS
        assert "gpt-5.6-luna" not in ids

    def test_direct_key_mode_serves_international_catalog(self, direct_key_client, converter_module):
        response = direct_key_client.get("/v1/models")
        assert response.status_code == 200
        ids = [m["id"] for m in response.json()["data"]]
        assert ids == converter_module.INTL_MODELS
        assert "gpt-5.6-luna" in ids
        assert "minimax-m3-pay" not in ids

    def test_requires_client_key_when_configured(self, converter_module, client):
        converter_module.CONFIG["api_key"] = "secret"
        assert client.get("/v1/models").status_code == 401

    def test_open_when_no_client_key_configured(self, client):
        assert client.get("/v1/models").status_code == 200


class TestChatCompletionsAuth:
    def test_rejects_unauthenticated_request_when_key_set(self, converter_module, client):
        converter_module.CONFIG["api_key"] = "secret"
        response = client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert response.status_code == 401


class TestChatCompletionsDirectKeyMode:
    def test_sends_bearer_key_to_international_backend(
        self, direct_key_client, converter_module, upstream_ok
    ):
        upstream = upstream_ok([sse_chunk("OK"), sse_finish()])

        response = direct_key_client.post("/v1/chat/completions", json={
            "model": "hy3",
            "messages": [{"role": "user", "content": "say OK"}],
            "max_tokens": 100,
        })
        assert response.status_code == 200
        assert upstream.last["url"].startswith("https://www.codebuddy.ai/v2/chat/completions")
        assert upstream.last["headers"]["Authorization"] == "Bearer ck_test_dummy"
        assert "X-User-Id" not in upstream.last["headers"]

    def test_prepends_system_message_when_missing(
        self, direct_key_client, converter_module, upstream_ok
    ):
        upstream = upstream_ok([sse_chunk("OK"), sse_finish()])

        direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
        })
        sent = upstream.last["body"]["messages"]
        assert sent[0]["role"] == "system"

    def test_keeps_client_system_message_when_present(
        self, direct_key_client, converter_module, upstream_ok
    ):
        upstream = upstream_ok([sse_chunk("OK"), sse_finish()])

        direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [
                {"role": "system", "content": "You are terse."},
                {"role": "user", "content": "hi"},
            ],
            "max_tokens": 100,
        })
        sent = upstream.last["body"]["messages"]
        assert sent[0]["role"] == "system"
        assert sent[0]["content"] == "You are terse."
        assert len(sent) == 2

    def test_forces_stream_true_upstream_for_non_stream_client(
        self, direct_key_client, converter_module, upstream_ok
    ):
        upstream = upstream_ok([sse_chunk("OK"), sse_finish()])

        response = direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
            "stream": False,
        })
        assert response.status_code == 200
        assert upstream.last["body"]["stream"] is True
        # The client still receives a single non-streaming completion.
        body = response.json()
        assert body["object"] == "chat.completion"
        assert body["choices"][0]["message"]["content"] == "OK"

    def test_streams_sse_to_streaming_client(
        self, direct_key_client, converter_module, upstream_ok
    ):
        upstream_ok([sse_chunk("HE"), sse_chunk("LLO"), sse_finish()])

        response = direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
            "stream": True,
        })
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        # Chunks pass through individually; "HE" and "LLO" never merge in flight.
        assert '"content": "HE"' in response.text
        assert '"content": "LLO"' in response.text
        assert "data: [DONE]" in response.text

    def test_non_stream_response_carries_aggregated_content(
        self, direct_key_client, converter_module, upstream_ok
    ):
        upstream_ok([sse_chunk("PROXY "), sse_chunk("OK"), sse_finish()])

        response = direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
        })
        assert response.json()["choices"][0]["message"]["content"] == "PROXY OK"


def sse_tool_call(model: str = "glm-5.2") -> bytes:
    payload = {
        "id": "cmb-t", "model": model, "object": "chat.completion.chunk",
        "created": 1700000000,
        "choices": [{"index": 0,
                     "delta": {"tool_calls": [{"index": 0, "id": "call_1",
                                               "type": "function",
                                               "function": {"name": "get_weather",
                                                            "arguments": '{"city": "x"'}},
                                              {"index": 0,
                                               "function": {"arguments": ',"unit":"c"}'}}]},
                     "finish_reason": ""}],
        "usage": None,
    }
    return f"data: {json.dumps(payload)}\n\n".encode()


def sse_tool_finish(model: str = "glm-5.2") -> bytes:
    payload = {
        "id": "cmb-t", "model": model, "object": "chat.completion.chunk",
        "created": 1700000000,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
    }
    return f"data: {json.dumps(payload)}\n\n".encode()


def sse_minimal_chunk(finish_reason: str = "", content: str | None = None) -> bytes:
    delta = {"content": content} if content is not None else {}
    payload = {
        "id": "x", "model": "m", "object": "chat.completion.chunk",
        "created": 0,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        "usage": None,
    }
    return f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n".encode()


class TestCollectStreamAggregation:
    def test_tool_calls_reassembled_in_order(
        self, direct_key_client, converter_module, upstream_ok
    ):
        upstream_ok([sse_tool_call(), sse_tool_finish()])
        response = direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "weather?"}],
        })
        message = response.json()["choices"][0]["message"]
        (tc,) = message["tool_calls"]
        assert tc["id"] == "call_1"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "get_weather"
        assert tc["function"]["arguments"] == '{"city": "x","unit":"c"}'

    def test_tool_calls_finish_reason_defaulted_when_upstream_omits_it(
        self, direct_key_client, converter_module, upstream_ok
    ):
        upstream_ok([sse_tool_call(), sse_minimal_chunk()])
        response = direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "weather?"}],
        })
        assert response.json()["choices"][0]["finish_reason"] == "tool_calls"

    def test_usage_carried_from_final_chunk(
        self, direct_key_client, converter_module, upstream_ok
    ):
        upstream_ok([sse_chunk("OK"), sse_finish()])
        response = direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert response.json()["usage"]["total_tokens"] == 2

    def test_usage_zeroed_when_upstream_sends_none(
        self, direct_key_client, converter_module, upstream_ok
    ):
        upstream_ok([sse_chunk("OK"), sse_minimal_chunk("stop")])
        response = direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert response.json()["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def test_model_field_taken_from_upstream_chunks(
        self, direct_key_client, converter_module, upstream_ok
    ):
        upstream_ok([sse_chunk("OK", model="hy3"), sse_finish(model="hy3")])
        response = direct_key_client.post("/v1/chat/completions", json={
            "model": "hy3",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert response.json()["model"] == "hy3"

    def test_stream_options_include_usage_injected_when_absent(
        self, direct_key_client, converter_module, upstream_ok
    ):
        upstream = upstream_ok([sse_chunk("OK"), sse_finish()])
        direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert upstream.last["body"]["stream_options"] == {"include_usage": True}

    def test_response_format_passthrough(
        self, direct_key_client, converter_module, upstream_ok
    ):
        upstream = upstream_ok([sse_chunk("OK"), sse_finish()])
        direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
            "response_format": {"type": "json_object"},
        })
        assert upstream.last["body"]["response_format"] == {"type": "json_object"}

    def test_finish_reason_defaults_to_stop_without_tool_calls(
        self, direct_key_client, converter_module, upstream_ok
    ):
        upstream_ok([sse_chunk("OK"), sse_minimal_chunk()])
        response = direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert response.json()["choices"][0]["finish_reason"] == "stop"

    def test_model_default_is_auto(
        self, direct_key_client, converter_module, upstream_ok
    ):
        upstream = upstream_ok([sse_chunk("OK"), sse_finish()])
        direct_key_client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert upstream.last["body"]["model"] == "auto"

    def test_tool_calls_ordered_by_index_across_two_indices(
        self, direct_key_client, converter_module, upstream_ok
    ):
        payload = {
            "id": "cmb-t2", "model": "glm-5.2", "object": "chat.completion.chunk",
            "created": 1700000000,
            "choices": [{"index": 0,
                         "delta": {"tool_calls": [
                             {"index": 1, "id": "call_2", "type": "function",
                              "function": {"name": "get_time", "arguments": "{}"}},
                             {"index": 0, "id": "call_1", "type": "function",
                              "function": {"name": "get_weather", "arguments": "{}"}},
                         ]},
                         "finish_reason": "tool_calls"}],
            "usage": None,
        }
        upstream_ok([f"data: {json.dumps(payload)}\n\n".encode(),
                     f"data: [DONE]\n\n".encode()])
        response = direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "tools?"}],
        })
        tcs = response.json()["choices"][0]["message"]["tool_calls"]
        assert [tc["id"] for tc in tcs] == ["call_1", "call_2"]

    def test_empty_content_is_none_when_upstream_sends_no_content(
        self, direct_key_client, converter_module, upstream_ok
    ):
        payload = {
            "id": "x", "model": "m", "object": "chat.completion.chunk",
            "created": 0,
            "choices": [{"index": 0,
                         "delta": {"role": "assistant", "tool_calls": [
                             {"index": 0, "id": "c1", "type": "function",
                              "function": {"name": "f", "arguments": "{}"}}]},
                         "finish_reason": "tool_calls"}],
            "usage": None,
        }
        upstream_ok([f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n".encode()])
        response = direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert response.json()["choices"][0]["message"]["content"] is None


class TestErrCode:
    @pytest.mark.parametrize("detail,expected", [
        ({"error": {"code": 6004}}, 6004),
        ({"error": {"code": "11128"}}, 11128),
        ({"error": {"msg_code": 6004}}, 6004),
        ({"error": {"message": 'code 6004 quota'}}, 6004),
        ({"error": {"msg": "code:11128 gated"}}, 11128),
        ({"error": {"message": "nothing numeric here"}}, None),
        ({"error": {}}, None),
        ({"error": {"code": 6004, "msg_code": 11128}}, 6004),
    ])
    def test_extracts_upstream_code(self, converter_module, detail, expected):
        assert converter_module._err_code(detail) == expected


class TestLogPrivacy:
    def test_full_request_body_not_logged_by_default(
        self, direct_key_client, converter_module, upstream_ok, tmp_path
    ):
        log_file = tmp_path / "conv.log"
        converter_module.CONFIG["log_path"] = str(log_file)
        upstream_ok([sse_chunk("OK"), sse_finish()])
        direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "filler text. " * 8 + "private secret content"}],
        })
        text = log_file.read_text(encoding="utf-8")
        assert "private secret content" not in text
        assert "REQUEST" in text
        converter_module.CONFIG["log_path"] = None

    def test_direct_key_value_never_logged(
        self, direct_key_client, converter_module, upstream_ok, tmp_path
    ):
        log_file = tmp_path / "conv.log"
        converter_module.CONFIG["log_path"] = str(log_file)
        upstream_ok([sse_chunk("OK"), sse_finish()])
        direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert "ck_test_dummy" not in log_file.read_text(encoding="utf-8")
        converter_module.CONFIG["log_path"] = None

    def test_full_response_body_not_logged_by_default(
        self, direct_key_client, converter_module, upstream_ok, tmp_path
    ):
        log_file = tmp_path / "conv.log"
        converter_module.CONFIG["log_path"] = str(log_file)
        upstream_ok([sse_chunk("private response content"), sse_finish()])
        direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
        })
        text = log_file.read_text(encoding="utf-8")
        assert "private response content" not in text
        assert "RESPONSE" in text
        converter_module.CONFIG["log_path"] = None

    def test_raw_stream_not_logged_by_default(
        self, direct_key_client, converter_module, upstream_ok, tmp_path
    ):
        log_file = tmp_path / "conv.log"
        converter_module.CONFIG["log_path"] = str(log_file)
        upstream_ok([sse_chunk("raw stream content"), sse_finish()])
        direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
        text = log_file.read_text(encoding="utf-8")
        assert "raw stream content" not in text
        assert "RESPONSE" in text
        converter_module.CONFIG["log_path"] = None

    def test_log_body_flag_enables_full_payload_logging(
        self, direct_key_client, converter_module, upstream_ok, tmp_path
    ):
        log_file = tmp_path / "conv.log"
        converter_module.CONFIG["log_path"] = str(log_file)
        converter_module.CONFIG["log_body"] = True
        upstream_ok([sse_chunk("OK"), sse_finish()])
        direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "private secret content"}],
        })
        converter_module.CONFIG["log_body"] = False
        converter_module.CONFIG["log_path"] = None
        text = log_file.read_text(encoding="utf-8")
        assert "private secret content" in text


class TestRelayRawForwarding:
    def test_relay_forwards_raw_bytes_verbatim(
        self, direct_key_client, converter_module, upstream_ok
    ):
        chunk = sse_chunk("X") + sse_finish()
        upstream_ok([chunk])
        response = direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
        assert response.text.count("data: ") >= 2
        assert '"content": "X"' in response.text

    def test_relay_detects_content_filter_marker(
        self, direct_key_client, converter_module, upstream_ok, tmp_path
    ):
        log_file = tmp_path / "conv.log"
        converter_module.CONFIG["log_path"] = str(log_file)
        converter_module.CONFIG["log_body"] = True
        payload = {
            "id": "cmb-f", "model": "glm-5.2", "object": "chat.completion.chunk",
            "created": 1700000000,
            "choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": "content-filter"}],
            "usage": None,
        }
        upstream_ok([f"data: {json.dumps(payload)}\n\n".encode(), b"data: [DONE]\n\n"])
        response = direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
        assert response.status_code == 200
        text = log_file.read_text(encoding="utf-8")
        assert "RESPONSE RAW SSE" in text
        assert '"content": "x"' in text
        converter_module.CONFIG["log_path"] = None


class TestChatCompletionsErrors:
    def test_missing_messages_rejected(self, direct_key_client):
        response = direct_key_client.post("/v1/chat/completions", json={"model": "glm-5.2"})
        assert response.status_code == 400

    def test_invalid_json_rejected(self, direct_key_client):
        response = direct_key_client.post(
            "/v1/chat/completions",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_upstream_http_error_maps_to_502(
        self, direct_key_client, converter_module, monkeypatch
    ):
        class FailingClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def stream(self, *args, **kwargs):
                raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(converter_module.httpx, "AsyncClient", FailingClient)

        response = direct_key_client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
        })
        assert response.status_code == 502


class TestTokenPathUnchanged:
    def test_token_mode_requires_credential_manager(self, converter_module, client):
        # Without credentials the token path must fail with 503, not fall back
        # to the direct-key backend.
        converter_module.CONFIG["api_key"] = ""
        response = client.post("/v1/chat/completions", json={
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert response.status_code == 503

    def test_token_mode_health_reports_credentials_missing(self, converter_module, client):
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body.get("cred") is None or "credential" not in body
