"""The only provider-egress boundary used by the local Core Service."""

from .credentials import (
    DEEPSEEK_CREDENTIAL_TARGET,
    PROVIDER_CREDENTIAL_TARGETS,
    CredentialRead,
    delete_provider_credential,
    read_deepseek_api_key,
    read_deepseek_credential,
    read_provider_api_key,
    read_provider_credential,
    write_provider_credential,
)
from .registry import (
    PROVIDER_NAMES,
    PROVIDER_PRESETS,
    provider_display_name,
    provider_preset,
)
from .deepseek import DeepSeekAdapter, OpenAICompatibleAdapter
from .runtime import (
    AgentRuntime,
    PayloadInspector,
    ProviderConfig,
    ProviderCompletion,
    ProviderFailure,
    ProviderFailureKind,
    ProviderResponse,
    ProviderRuntimeSet,
    StreamConsumerDisconnected,
)

__all__ = [
    "AgentRuntime",
    "DEEPSEEK_CREDENTIAL_TARGET",
    "PROVIDER_CREDENTIAL_TARGETS",
    "PROVIDER_NAMES",
    "PROVIDER_PRESETS",
    "CredentialRead",
    "DeepSeekAdapter",
    "OpenAICompatibleAdapter",
    "PayloadInspector",
    "ProviderConfig",
    "ProviderCompletion",
    "ProviderFailure",
    "ProviderFailureKind",
    "ProviderResponse",
    "ProviderRuntimeSet",
    "StreamConsumerDisconnected",
    "delete_provider_credential",
    "provider_display_name",
    "provider_preset",
    "read_deepseek_api_key",
    "read_deepseek_credential",
    "read_provider_api_key",
    "read_provider_credential",
    "write_provider_credential",
]
