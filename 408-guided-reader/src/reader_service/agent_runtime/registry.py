"""Named provider presets for the OpenAI-compatible chat boundary.

This module is the single source of truth for which providers exist, their
default endpoints/models, and their Windows Credential Manager targets. The
runtime, credentials, and adapter all read from this registry; adding a
provider means adding an entry here and nothing else.
"""

from __future__ import annotations


def _preset(
    display_name: str,
    endpoint: str,
    default_model: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    timeout_seconds: float = 120.0,
    optional_proxy: bool = False,
) -> dict:
    return {
        "display_name": display_name,
        "endpoint": endpoint,
        "default_model": default_model,
        "credential_target": f"408-guided-reader-{_name}",
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout_seconds": timeout_seconds,
        "optional_proxy": optional_proxy,
    }


# Built via a factory loop below so credential targets stay derived from the
# provider name itself and can never drift between the two spellings.
_PRESET_INPUTS: dict[str, tuple] = {
    # Legacy providers keep their exact existing configuration.
    "deepseek": ("DeepSeek", "https://api.deepseek.com/chat/completions", "deepseek-flash"),
    "zhipu": (
        "智谱 GLM",
        "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "GLM-5.3-Flash",
    ),
    # OpenRouter keeps its historical lighter budget; the proxy is now optional
    # (direct connection by default) and there is no model whitelist.
    "openrouter": (
        "OpenRouter",
        "https://openrouter.ai/api/v1/chat/completions",
        "google/gemini-3.8-flash",
        {"max_tokens": 900, "timeout_seconds": 45.0, "optional_proxy": True},
    ),
    "qwen": (
        "通义千问",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "qwen-plus",
    ),
    "moonshot": ("Kimi", "https://api.moonshot.cn/v1/chat/completions", "moonshot-v1-8k"),
    # Volcano Ark requires the user's own endpoint id (ep-xxx) as the model.
    "doubao": (
        "豆包（火山方舟）",
        "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "",
    ),
    "minimax": (
        "MiniMax",
        "https://api.minimax.chat/v1/text/chatcompletion_v2",
        "abab6.5s-chat",
    ),
    "spark": (
        "讯飞星火",
        "https://spark-api-open.xf-yun.com/v1/chat/completions",
        "generalv3.5",
    ),
    "hunyuan": (
        "腾讯混元",
        "https://api.hunyuan.cloud.tencent.com/v1/chat/completions",
        "hunyuan-turbo",
    ),
    "siliconflow": (
        "硅基流动",
        "https://api.siliconflow.cn/v1/chat/completions",
        "deepseek-ai/DeepSeek-V3",
    ),
    # User-supplied endpoint and model; configured via environment variables.
    "custom": ("自定义", "", ""),
}

PROVIDER_PRESETS: dict[str, dict] = {}
for _name, _values in _PRESET_INPUTS.items():
    _display, _endpoint, _model = _values[0], _values[1], _values[2]
    _overrides = _values[3] if len(_values) > 3 else {}
    PROVIDER_PRESETS[_name] = _preset(
        _display, _endpoint, _model, **_overrides
    )

PROVIDER_NAMES: tuple[str, ...] = tuple(PROVIDER_PRESETS)


def provider_preset(provider: str) -> dict:
    """Return the preset for a known provider or raise ValueError."""

    try:
        return PROVIDER_PRESETS[provider]
    except KeyError as error:
        raise ValueError("unknown provider") from error


def provider_display_name(provider: str) -> str:
    return provider_preset(provider)["display_name"]
