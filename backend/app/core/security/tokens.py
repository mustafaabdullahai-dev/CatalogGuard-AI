"""Opaque single-use tokens (D-017).

Shared by session cookies, password resets, and workspace invitations.

Why the same construction everywhere
    All three are bearer secrets: whoever holds the value gets the authority. So
    all three get the same treatment, in one file, rather than three slightly
    different implementations that drift.

Why SHA-256 and not Argon2
    The input is 256 bits of CSPRNG output, so there is no dictionary to attack
    and no work factor worth paying. Argon2 exists to make guessing a
    human-chosen secret expensive; a random 256-bit token cannot be guessed at
    all. Storing only the digest means a database disclosure yields nothing
    usable.

Why single-use is enforced in SQL, not in the application
    `consume_token` is a conditional UPDATE whose WHERE clause carries the
    "not already consumed and not expired" test. Two concurrent requests
    presenting the same token therefore produce exactly one winner, with no
    read-then-write window for a second caller to slip into.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

# 32 bytes = 256 bits, URL-safe. Comfortably past any brute-force horizon.
TOKEN_BYTES = 32


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Digest a token for storage and lookup."""
    return hashlib.sha256(token.encode()).hexdigest()


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A freshly minted token: the record to persist and the plaintext to send.

    Returned together deliberately, so the plaintext exists in exactly one
    place at the moment it is first needed, and never survives in a log.
    """

    record: object
    token: str
