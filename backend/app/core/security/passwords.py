"""Password hashing (D-014).

Argon2id, with the parameters recorded inside each hash so they can be raised
later without invalidating anyone's password.

Why Argon2id
    Memory-hard. bcrypt and scrypt are memory-hard too, but Argon2id is the
    current recommendation and has no 72-byte input limit, so a long passphrase
    is not silently truncated to its first 72 bytes.

Why parameters live in the hash
    A stored hash records the cost that produced it. Verification uses the
    stored parameters, so raising the configured defaults only affects new
    passwords; `needs_rehash` then upgrades an existing one the next time its
    owner logs in successfully. Raising the cost is therefore a migration with
    no user-visible interruption and no forced reset.
"""

from __future__ import annotations

from contextlib import suppress
from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

from app.core.config import get_settings

# argon2 signals "no match" and "this is not a hash I can check" with different
# exceptions, and both mean the same thing to someone authenticating a caller.
_HASH_ERRORS = (VerifyMismatchError, VerificationError, InvalidHashError)


@cache
def _dummy_hash() -> str:
    """A real hash of a value nobody can supply, generated once per process.

    It has to be a genuine Argon2id hash. Verifying a malformed string raises
    before any work happens, which would skip the CPU cost this exists to spend
    and reintroduce the timing leak it prevents.
    """
    return _hasher().hash("cg-timing-equalizer-not-a-credential")


class PasswordPolicyError(ValueError):
    """Raised before hashing, not after. Never leak this to the client."""


def _hasher() -> PasswordHasher:
    settings = get_settings()
    return PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_cost_kib,
        parallelism=settings.argon2_parallelism,
        hash_len=settings.argon2_hash_len,
        salt_len=settings.argon2_salt_len,
        type=Type.ID,
    )


def validate_password(password: str) -> None:
    """Reject passwords that would weaken the scheme regardless of hashing.

    Deliberately not a composition rule. Forcing a symbol produces `Password1!`
    far more often than it produces a memorable passphrase, and the length
    floor below does more for resistance than the character classes do.
    """
    settings = get_settings()
    if len(password) < settings.password_min_length:
        raise PasswordPolicyError(
            f"password must be at least {settings.password_min_length} characters"
        )
    if len(password) > 1024:
        # Bounds the cost of an Argon2 call triggered by a huge body. 1 KiB of
        # entropy far exceeds the floor above; more is a paste accident.
        raise PasswordPolicyError("password is unreasonably long")
    if password.strip() != password:
        raise PasswordPolicyError("password must not begin or end with whitespace")


def hash_password(password: str) -> str:
    """Hash with a fresh random salt. Returns the encoded hash."""
    validate_password(password)
    return _hasher().hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """Check a password against a stored hash.

    A malformed stored hash returns False rather than raising. Someone reaching
    this state has already lost the hash, so the honest outcome is "this
    credential does not work", and a raised exception would turn a broken row
    into a 500 that looks like an attack in the logs.
    """
    try:
        _hasher().verify(stored_hash, password)
    except _HASH_ERRORS:
        return False
    return True


def needs_rehash(stored_hash: str) -> bool:
    """True when the stored hash predates the current cost parameters.

    Called on successful login only. Rehashing on every request would make the
    password the most expensive thing in the hot path; rehashing only when the
    user has already proved they know the password keeps it free.
    """
    try:
        return _hasher().check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def dummy_verify() -> None:
    """Burn the same CPU a real verification would.

    Called when no user matched the submitted address, so that "no such user"
    and "wrong password" take the same wall-clock time. Without it, response
    latency enumerates registered addresses.
    """
    with suppress(*_HASH_ERRORS):
        _hasher().verify(_dummy_hash(), "not-a-real-password")
