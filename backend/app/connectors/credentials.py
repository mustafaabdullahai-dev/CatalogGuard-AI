"""Credential encryption at rest (Phase 19, §33, D-005).

Why a function rather than a column default
    `marketplace_connections.encrypted_credentials` stores ciphertext and has
    no plaintext twin (§33: "Marketplace credentials must never be stored as
    plaintext"), so the only way for a secret to reach the database is through
    here — one choke point that a test can hold. A column default would apply
    only when a caller happened to omit the value, which is not a guarantee.

Why the key comes from settings and is never an argument
    Passing the key in would mean every call site handles it, logs it, or
    forgets it. `ENCRYPTION_KEY` is already validated as a Fernet key at
    startup, so a deployment with a placeholder cannot reach this code at all.

Why decryption failures are their own error
    A blob that will not decrypt means either the key rotated or the row was
    tampered with. Reporting it as `InvalidToken` (Fernet's own exception)
    would leak an implementation detail into the execution path; reporting it
    as a generic failure would hide that the credentials themselves are the
    problem. `CredentialError` says exactly that much and no more.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class CredentialError(RuntimeError):
    """Stored credentials could not be written or read back."""


def _fernet() -> Fernet:
    return Fernet(get_settings().encryption_key.encode("utf-8"))


def _as_secret_text(credentials: Mapping[str, str]) -> bytes:
    """Canonical JSON, so equal credential sets produce equal plaintext.

    Keys sorted and separators tightened: ciphertext that depends on dict
    ordering would make "did this credential change?" unanswerable by
    comparing two blobs.
    """
    if not isinstance(credentials, Mapping) or not credentials:
        raise CredentialError("credentials must be a non-empty mapping")
    for key, value in credentials.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise CredentialError("credential keys and values must be strings")
        if not key.strip():
            # An unnamed credential is either a caller's bug or a value it
            # meant to keep secret and mislabeled. Neither belongs at rest.
            raise CredentialError("credential keys must not be blank")
    return json.dumps(
        dict(credentials), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def encrypt_credentials(credentials: Mapping[str, str]) -> str:
    """Encrypt a credential set for `marketplace_connections`."""
    return _fernet().encrypt(_as_secret_text(credentials)).decode("ascii")


def decrypt_credentials(encrypted: str) -> dict[str, str]:
    """Read back a blob written by `encrypt_credentials`.

    Raises `CredentialError` rather than Fernet's `InvalidToken`, so callers
    and logs never have to know which library is in use.
    """
    if not isinstance(encrypted, str) or not encrypted:
        raise CredentialError("no stored credentials to decrypt")
    try:
        plaintext = _fernet().decrypt(encrypted.encode("ascii"))
    except (InvalidToken, ValueError) as exc:
        raise CredentialError(
            "stored credentials cannot be decrypted with this ENCRYPTION_KEY"
        ) from exc
    try:
        decoded = json.loads(plaintext)
    except json.JSONDecodeError as exc:
        raise CredentialError("stored credentials are not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise CredentialError("stored credentials are not a mapping")
    return {str(key): str(value) for key, value in decoded.items()}
