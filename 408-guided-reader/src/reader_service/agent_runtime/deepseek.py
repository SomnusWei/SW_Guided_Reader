"""Compatibility module: the provider adapter now lives in openai_compat.py.

Everything is re-exported unchanged so existing imports
(`reader_service.agent_runtime.deepseek`) keep working.
"""

from __future__ import annotations

from .openai_compat import (  # noqa: F401
    DeepSeekAdapter,
    OpenAICompatibleAdapter,
    _StrictProxyHandler,
    _configured_openrouter_proxy,
    _is_loopback_endpoint,
    _iter_sse_data,
    _windows_user_proxy,
    build_opener,
    proxy_bypass,
)

__all__ = [
    "DeepSeekAdapter",
    "OpenAICompatibleAdapter",
]
