from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass, field

from .registry import PROVIDER_PRESETS


# Derived from the registry so a new provider automatically gets the
# `408-guided-reader-{provider}` Credential Manager target and the
# `GUIDED_READER_{PROVIDER}_API_KEY` environment fallback.
PROVIDER_CREDENTIAL_TARGETS = {
    name: preset["credential_target"] for name, preset in PROVIDER_PRESETS.items()
}
PROVIDER_KEY_ENVS = {
    name: f"GUIDED_READER_{name.upper()}_API_KEY" for name in PROVIDER_PRESETS
}
DEEPSEEK_CREDENTIAL_TARGET = PROVIDER_CREDENTIAL_TARGETS["deepseek"]
DEVELOPMENT_KEY_ENV = PROVIDER_KEY_ENVS["deepseek"]


@dataclass(frozen=True, slots=True)
class CredentialRead:
    """Secret-bearing credential result whose representation is always safe."""

    available: bool
    source: str
    reason: str | None
    key: str | None = field(default=None, repr=False)


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def read_provider_credential(provider: str) -> CredentialRead:
    """Read a named provider credential with bounded, secret-safe diagnostics."""

    try:
        target = PROVIDER_CREDENTIAL_TARGETS[provider]
        key_env = PROVIDER_KEY_ENVS[provider]
    except KeyError as error:
        raise ValueError("unknown provider") from error
    prefix = provider.upper()
    if os.environ.get(f"GUIDED_READER_{prefix}_DISABLED", "").strip() == "1":
        return CredentialRead(False, "NONE", "DEVELOPMENT_DISABLED")
    override = os.environ.get(key_env, "").strip()
    if override:
        return CredentialRead(True, "DEVELOPMENT_OVERRIDE", None, override)
    if sys.platform != "win32":
        return CredentialRead(False, "NONE", "UNSUPPORTED_PLATFORM")
    try:
        advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        credential = ctypes.POINTER(_CREDENTIALW)()
        cred_read = advapi32.CredReadW
        cred_read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        cred_read.restype = wintypes.BOOL
        cred_free = advapi32.CredFree
        cred_free.argtypes = [ctypes.c_void_p]
        if not cred_read(target, 1, 0, ctypes.byref(credential)):
            reason = "CREDENTIAL_NOT_FOUND" if ctypes.get_last_error() == 1168 else "CREDENTIAL_READ_FAILED"
            return CredentialRead(False, "WINDOWS_CREDENTIAL_MANAGER", reason)
        try:
            size = int(credential.contents.CredentialBlobSize)
            if size <= 0 or not credential.contents.CredentialBlob:
                return CredentialRead(False, "WINDOWS_CREDENTIAL_MANAGER", "CREDENTIAL_EMPTY")
            raw = ctypes.string_at(credential.contents.CredentialBlob, size)
            # Windows generic credentials written by Credential Manager are normally
            # UTF-16LE. A UTF-8 fallback keeps programmatic generic credentials usable.
            try:
                value = raw.decode("utf-16-le") if b"\x00" in raw else raw.decode("utf-8")
            except UnicodeDecodeError:
                return CredentialRead(False, "WINDOWS_CREDENTIAL_MANAGER", "CREDENTIAL_READ_FAILED")
            key = value.rstrip("\x00").strip()
            if not key:
                return CredentialRead(False, "WINDOWS_CREDENTIAL_MANAGER", "CREDENTIAL_EMPTY")
            return CredentialRead(True, "WINDOWS_CREDENTIAL_MANAGER", None, key)
        finally:
            cred_free(credential)
    except (AttributeError, OSError, ValueError):
        return CredentialRead(False, "WINDOWS_CREDENTIAL_MANAGER", "CREDENTIAL_READ_FAILED")


def read_deepseek_credential() -> CredentialRead:
    """Compatibility wrapper for the original provider-specific API."""

    return read_provider_credential("deepseek")


def read_deepseek_api_key() -> str | None:
    """Compatibility API returning the key without exposing diagnostic internals."""

    return read_deepseek_credential().key


def read_provider_api_key(provider: str) -> str | None:
    return read_provider_credential(provider).key


_CRED_PERSIST_ENTERPRISE = 2  # 用户凭据（与凭据管理器 UI 手工创建的默认类型一致）
_CRED_TYPE_GENERIC = 1


def write_provider_credential(provider: str, api_key: str) -> None:
    """Store ``api_key`` in Windows Credential Manager under the provider target.

    Raises ``ValueError`` for unknown providers or empty keys, and ``OSError``
    when the Win32 write fails. Mirrors what the user would create by hand in
    凭据管理器 → 添加"用户凭据".
    """

    try:
        target = PROVIDER_CREDENTIAL_TARGETS[provider]
    except KeyError as error:
        raise ValueError("unknown provider") from error
    key = str(api_key).strip()
    if not key:
        raise ValueError("api_key 不能为空")
    if sys.platform != "win32":
        raise OSError("WINDOWS_CREDENTIAL_MANAGER_UNAVAILABLE")
    try:
        advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        cred_write = advapi32.CredWriteW
        cred_write.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        cred_write.restype = wintypes.BOOL
        blob = key.encode("utf-16-le")
        blob_buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        credential = _CREDENTIALW()
        credential.Flags = 0
        credential.Type = _CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.Comment = "Guided Reader provider API key"
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(blob_buffer, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = _CRED_PERSIST_ENTERPRISE
        credential.AttributeCount = 0
        credential.Attributes = None
        credential.TargetAlias = None
        credential.UserName = "reader"
        if not cred_write(ctypes.byref(credential), 0):
            raise OSError(f"CREDENTIAL_WRITE_FAILED ({ctypes.get_last_error()})")
    except (AttributeError, OSError, ValueError):
        # 非 Win32 平台、Advapi32 缺失或 blob 构造失败统一抛 OSError/ValueError
        raise


def delete_provider_credential(provider: str) -> None:
    """Remove the stored credential for ``provider``. Missing entries are a no-op."""

    try:
        target = PROVIDER_CREDENTIAL_TARGETS[provider]
    except KeyError as error:
        raise ValueError("unknown provider") from error
    if sys.platform != "win32":
        raise OSError("WINDOWS_CREDENTIAL_MANAGER_UNAVAILABLE")
    try:
        advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        cred_delete = advapi32.CredDeleteW
        cred_delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        cred_delete.restype = wintypes.BOOL
        if not cred_delete(target, _CRED_TYPE_GENERIC, 0):
            error_code = ctypes.get_last_error()
            if error_code != 1168:  # ERROR_NOT_FOUND: already absent is fine
                raise OSError(f"CREDENTIAL_DELETE_FAILED ({error_code})")
    except (AttributeError, OSError):
        raise
