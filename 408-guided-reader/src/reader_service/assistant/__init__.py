"""Temporary Reader-native Assistant state and context assembly."""

from .context import AssistantContextBuilder, ScopeResolution, ScopeResolver
from .history import AssistantHistoryService
from .service import AssistantService, AssistantStateError, SelectionSourceKind

__all__ = [
    "AssistantContextBuilder",
    "AssistantHistoryService",
    "AssistantService",
    "AssistantStateError",
    "ScopeResolution",
    "ScopeResolver",
    "SelectionSourceKind",
]
