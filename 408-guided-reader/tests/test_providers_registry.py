"""Registry-driven providers and the generalized OpenAI-compatible client."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import pytest

from reader_service.agent_runtime import (
    AgentRuntime,
    OpenAICompatibleAdapter,
    ProviderConfig,
    ProviderFailure,
    ProviderFailureKind,
    read_provider_api_key,
    read_provider_credential,
)
from reader_service.agent_runtime.credentials import (
    PROVIDER_CREDENTIAL_TARGETS,
    PROVIDER_KEY_ENVS,
)
from reader_service.agent_runtime.openai_compat import _StrictProxyHandler
from reader_service.agent_runtime.registry import (
    PROVIDER_NAMES,
    PROVIDER_PRESETS,
    provider_display_name,
    provider_preset,
)
from reader_service.agent_runtime.runtime import PROVIDER_DEFAULTS


LEGACY_PROVIDERS = ("deepseek", "zhipu", "openrouter")
NEW_PROVIDERS = (
    "qwen", "moonshot", "doubao", "minimax", "spark", "hunyuan", "siliconflow", "custom",
)
EMPTY_MODEL_PROVIDERS = ("doubao", "custom")


def test_registry_contains_legacy_and_new_providers():
    assert PROVIDER_NAMES[:3] == LEGACY_PROVIDERS
    assert set(PROVIDER_NAMES) == set(LEGACY_PROVIDERS) | set(NEW_PROVIDERS)
    assert len(PROVIDER_NAMES) == len(set(PROVIDER_NAMES))


def test_registry_entries_are_complete_and_consistent():
    for name, preset in PROVIDER_PRESETS.items():
        assert preset["display_name"]
        assert preset["credential_target"] == f"408-guided-reader-{name}"
        if name != "custom":
            assert preset["endpoint"].startswith("https://")
        if name not in EMPTY_MODEL_PROVIDERS:
            assert preset["default_model"]
        assert 0 <= preset["temperature"] <= 2
        assert 1 <= preset["max_tokens"] <= 4096
        assert 1 <= preset["timeout_seconds"] <= 180
        assert isinstance(preset["optional_proxy"], bool)


def test_registry_keeps_existing_legacy_configuration():
    assert provider_preset("deepseek")["endpoint"] == "https://api.deepseek.com/chat/completions"
    assert provider_preset("deepseek")["default_model"] == "deepseek-flash"
    assert provider_preset("zhipu")["endpoint"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert provider_preset("zhipu")["default_model"] == "GLM-5.3-Flash"
    assert provider_preset("openrouter")["endpoint"] == "https://openrouter.ai/api/v1/chat/completions"
    assert provider_preset("openrouter")["default_model"] == "google/gemini-3.8-flash"
    assert provider_preset("openrouter")["max_tokens"] == 900
    assert provider_preset("openrouter")["optional_proxy"] is True
    assert not any(
        preset["optional_proxy"]
        for name, preset in PROVIDER_PRESETS.items() if name != "openrouter"
    )


def test_new_provider_endpoints_and_models():
    assert provider_preset("qwen")["endpoint"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
    assert provider_preset("qwen")["default_model"] == "qwen-plus"
    assert provider_preset("moonshot")["endpoint"] == "https://api.moonshot.cn/v1/chat/completions"
    assert provider_preset("moonshot")["default_model"] == "moonshot-v1-8k"
    assert provider_preset("doubao")["endpoint"] == (
        "https://ark.cn-beijing.volces.com/api/v3/chat/completions")
    assert provider_preset("doubao")["default_model"] == ""
    assert provider_preset("minimax")["endpoint"] == "https://api.minimax.chat/v1/text/chatcompletion_v2"
    assert provider_preset("minimax")["default_model"] == "abab6.5s-chat"
    assert provider_preset("spark")["default_model"] == "generalv3.5"
    assert provider_preset("hunyuan")["default_model"] == "hunyuan-turbo"
    assert provider_preset("siliconflow")["default_model"] == "deepseek-ai/DeepSeek-V3"
    assert provider_preset("custom")["endpoint"] == ""
    assert provider_preset("custom")["default_model"] == ""


def test_runtime_defaults_are_derived_from_registry():
    assert set(PROVIDER_DEFAULTS) == set(PROVIDER_PRESETS)
    for name, preset in PROVIDER_PRESETS.items():
        assert PROVIDER_DEFAULTS[name]["endpoint"] == preset["endpoint"]
        assert PROVIDER_DEFAULTS[name]["model"] == preset["default_model"]


def test_unknown_provider_is_rejected_everywhere():
    with pytest.raises(ValueError, match="unknown provider"):
        ProviderConfig.from_environment("nope")
    with pytest.raises(ValueError, match="unknown provider"):
        provider_preset("nope")
    with pytest.raises(ValueError, match="unknown provider"):
        provider_display_name("nope")
    with pytest.raises(ValueError, match="unknown provider"):
        read_provider_credential("nope")
    config = ProviderConfig(provider="nope")
    with pytest.raises(ValueError, match="named set"):
        config.validate()


def test_from_environment_supports_any_registry_provider(monkeypatch):
    monkeypatch.delenv("GUIDED_READER_QWEN_API_KEY", raising=False)
    config = ProviderConfig.from_environment("qwen")
    assert config.endpoint == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert config.model == "qwen-plus"
    assert config.credential_target == "408-guided-reader-qwen"
    assert config.temperature == 0.2
    assert config.max_tokens == 4096
    assert config.timeout_seconds == 120.0
    config.validate()

    monkeypatch.setenv("GUIDED_READER_MOONSHOT_MODEL", "kimi-k2")
    monkeypatch.setenv("GUIDED_READER_MOONSHOT_MAX_TOKENS", "2048")
    overridden = ProviderConfig.from_environment("moonshot")
    assert overridden.model == "kimi-k2"
    assert overridden.max_tokens == 2048
    overridden.validate()

    # doubao/custom ship empty models on purpose and stay unconfigured until
    # the user supplies the endpoint id / custom model.
    doubao = ProviderConfig.from_environment("doubao")
    assert doubao.model == ""
    with pytest.raises(ValueError, match="model is invalid"):
        doubao.validate()
    custom = ProviderConfig.from_environment("custom")
    assert custom.model == "" and custom.endpoint == ""
    with pytest.raises(ValueError):
        custom.validate()


def test_credential_targets_and_env_names_cover_every_provider():
    assert set(PROVIDER_CREDENTIAL_TARGETS) == set(PROVIDER_NAMES)
    assert set(PROVIDER_KEY_ENVS) == set(PROVIDER_NAMES)
    for name in PROVIDER_NAMES:
        assert PROVIDER_CREDENTIAL_TARGETS[name] == f"408-guided-reader-{name}"
        assert PROVIDER_KEY_ENVS[name] == f"GUIDED_READER_{name.upper()}_API_KEY"


def test_environment_credential_fallback_works_for_new_providers(monkeypatch):
    monkeypatch.setenv("GUIDED_READER_SILICONFLOW_API_KEY", "flow-key")
    assert read_provider_api_key("siliconflow") == "flow-key"
    monkeypatch.setenv("GUIDED_READER_SILICONFLOW_DISABLED", "1")
    assert read_provider_api_key("siliconflow") is None


class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        status, payload, content_type = self.routes[self.path]
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output clean
        pass


@pytest.fixture
def provider_server():
    routes = {
        "/ok": (200, {"choices": [{"message": {"content": "普通回答"},
                                   "finish_reason": "stop"}],
                      "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}},
                "application/json"),
        "/length": (200, {"choices": [{"message": {"content": "被截断"},
                                       "finish_reason": "length"}]}, "application/json"),
        "/empty": (200, {"choices": [{"message": {"content": " "},
                                      "finish_reason": "stop"}]}, "application/json"),
        "/bad-json": (200, b"not-json{", "application/json"),
        "/auth": (401, {"error": {"message": "invalid key"}}, "application/json"),
        "/quota": (402, {"error": {"message": "balance insufficient"}}, "application/json"),
        "/busy": (429, {"error": {"message": "rate limited"}}, "application/json"),
        "/region": (403, {"error": {"message": "model not available in your region"}},
                    "application/json"),
        "/minimax-wrapped": (200, {"data": {"choices": [{"message": {"content": "包装回答"},
                                                        "finish_reason": "stop"}]}},
                             "application/json"),
        "/minimax-error": (200, {"base_resp": {"status_code": 1004, "status_msg": "invalid"}},
                           "application/json"),
        "/sse": (200, ("data: " + json.dumps({
            "choices": [{"delta": {"reasoning_content": "思考"}, "finish_reason": None}]}) + "\n\n"
            + "data: " + json.dumps({
                "choices": [{"delta": {"content": "流式"}, "finish_reason": None}]}) + "\n\n"
            + "data: " + json.dumps({
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}}) + "\n\n"
            + "data: [DONE]\n\n").encode("utf-8"), "text/event-stream"),
    }
    _Handler.routes = routes
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def runtime_for(provider: str, endpoint: str) -> AgentRuntime:
    config = replace(
        ProviderConfig.from_environment(provider),
        endpoint=endpoint,
        max_attempts=1,
    )
    config.validate()
    return AgentRuntime(
        OpenAICompatibleAdapter(provider),
        config=config,
        credential_loader=lambda: "fixture-key",
        sleeper=lambda _seconds: None,
    )


def test_non_streaming_completion_against_local_openai_service(provider_server):
    runtime = runtime_for("qwen", f"{provider_server}/ok")
    result = runtime.complete_with_metadata([{"role": "user", "content": "问题"}])
    assert result.answer == "普通回答"
    assert result.usage == {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}
    assert result.response_metadata["finish_reason"] == "stop"


def test_streaming_completion_against_local_openai_service(provider_server):
    runtime = runtime_for("deepseek", f"{provider_server}/sse")
    deltas: list[str] = []
    reasoning: list[str] = []
    result = runtime.stream_with_metadata(
        [{"role": "user", "content": "问题"}], deltas.append, reasoning.append)
    assert deltas == ["流式"]
    assert reasoning == ["思考"]
    assert result.answer == "流式"
    assert result.usage == {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}
    assert result.ttft_ms is not None


@pytest.mark.parametrize("path,code,kind", [
    ("/auth", "auth", ProviderFailureKind.USER_ACTIONABLE),
    ("/quota", "quota", ProviderFailureKind.USER_ACTIONABLE),
    ("/busy", "remote_busy", ProviderFailureKind.TRANSIENT),
    ("/region", "model_region", ProviderFailureKind.USER_ACTIONABLE),
])
def test_http_error_classification(provider_server, path, code, kind):
    runtime = runtime_for("zhipu", f"{provider_server}{path}")
    with pytest.raises(ProviderFailure) as caught:
        runtime.complete([{"role": "user", "content": "问题"}])
    assert caught.value.code == code
    assert caught.value.kind is kind
    assert "fixture-key" not in str(caught.value)


def test_error_message_uses_registry_display_name(provider_server):
    runtime = runtime_for("qwen", f"{provider_server}/auth")
    with pytest.raises(ProviderFailure) as caught:
        runtime.complete([{"role": "user", "content": "问题"}])
    assert "通义千问" in caught.value.user_message


def test_invalid_payload_is_transient(provider_server):
    runtime = runtime_for("zhipu", f"{provider_server}/bad-json")
    with pytest.raises(ProviderFailure) as caught:
        runtime.complete([{"role": "user", "content": "问题"}])
    assert caught.value.code == "invalid_response"
    assert caught.value.kind is ProviderFailureKind.TRANSIENT


def test_length_limited_and_empty_responses_are_classified(provider_server):
    truncated = runtime_for("deepseek", f"{provider_server}/length")
    with pytest.raises(ProviderFailure) as caught:
        truncated.complete([{"role": "user", "content": "问题"}])
    assert caught.value.code == "response_length_limit"

    empty = runtime_for("deepseek", f"{provider_server}/empty")
    with pytest.raises(ProviderFailure) as caught:
        empty.complete([{"role": "user", "content": "问题"}])
    assert caught.value.code == "empty_response"


def test_minimax_nonstandard_payload_shapes_are_tolerated(provider_server):
    wrapped = runtime_for("minimax", f"{provider_server}/minimax-wrapped")
    assert wrapped.complete([{"role": "user", "content": "问题"}]) == "包装回答"

    failed = runtime_for("minimax", f"{provider_server}/minimax-error")
    with pytest.raises(ProviderFailure) as caught:
        failed.complete([{"role": "user", "content": "问题"}])
    assert caught.value.code == "provider_request"
    assert caught.value.kind is ProviderFailureKind.USER_ACTIONABLE


def test_openai_compat_stream_rejects_non_sse_content_type(provider_server):
    runtime = runtime_for("deepseek", f"{provider_server}/ok")
    with pytest.raises(ProviderFailure) as caught:
        runtime.stream_with_metadata([{"role": "user", "content": "问题"}], lambda _delta: None)
    assert caught.value.code == "invalid_stream_response"


def test_unreachable_endpoint_is_transient_network_failure(provider_server):
    # Port 1 on loopback is closed; the classifier must stay TRANSIENT/network.
    runtime = runtime_for("qwen", "http://127.0.0.1:1/v1/chat/completions")
    with pytest.raises(ProviderFailure) as caught:
        runtime.complete([{"role": "user", "content": "问题"}])
    assert caught.value.code == "network"
    assert caught.value.kind is ProviderFailureKind.TRANSIENT


def test_status_exposes_registry_display_name_for_every_provider():
    for name, preset in PROVIDER_PRESETS.items():
        runtime = AgentRuntime(
            OpenAICompatibleAdapter(name),
            config=ProviderConfig(
                provider=name,
                endpoint=preset["endpoint"] or "https://example.test/v1/chat/completions",
                model=preset["default_model"] or "placeholder-model",
                credential_target=preset["credential_target"],
            ),
            credential_loader=lambda: "fixture-key",
        )
        status = runtime.status()
        assert status["display_name"] == preset["display_name"]
        assert status["credential_target"] == f"408-guided-reader-{name}"


def test_optional_proxy_is_openrouter_only_and_never_fail_closed(monkeypatch):
    import reader_service.agent_runtime.openai_compat as compat

    monkeypatch.setattr(compat, "_windows_user_proxy", lambda: "")
    monkeypatch.delenv("GUIDED_READER_OPENROUTER_PROXY", raising=False)
    external = "https://external.example/v1/chat/completions"
    for provider in PROVIDER_NAMES:
        handler = OpenAICompatibleAdapter(provider)._proxy_handler(external)
        assert not isinstance(handler, _StrictProxyHandler), provider

    monkeypatch.setenv("GUIDED_READER_OPENROUTER_PROXY", "http://127.0.0.1:7890")
    handler = OpenAICompatibleAdapter("openrouter")._proxy_handler(external)
    assert isinstance(handler, _StrictProxyHandler)
