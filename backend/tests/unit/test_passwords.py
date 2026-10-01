"""Password hashing tests (D-014).

Argon2 runs for real here. The properties worth testing — that a wrong password
fails, that a salt makes two hashes of the same password differ, that stored
parameters drive verification — cannot be asserted against a mock, because a
mock would agree with whatever the code did.
"""

from __future__ import annotations

import pytest

from app.core.security.passwords import (
    PasswordPolicyError,
    dummy_verify,
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)

GOOD = "correct horse battery staple"


class TestHashing:
    def test_correct_password_verifies(self) -> None:
        stored = hash_password(GOOD)
        assert verify_password(stored, GOOD)

    def test_wrong_password_does_not(self) -> None:
        stored = hash_password(GOOD)
        assert not verify_password(stored, "correct horse battery stapl")

    def test_same_password_hashes_differently(self) -> None:
        """A per-hash salt.

        Identical hashes for identical passwords would let one stolen row be
        checked against every other account that shares that password, and would
        make two users' rows visibly identical.
        """
        assert hash_password(GOOD) != hash_password(GOOD)

    def test_hash_is_not_the_password(self) -> None:
        stored = hash_password(GOOD)
        assert GOOD not in stored
        assert stored.startswith("$argon2id$")

    def test_argon2id_not_argon2i(self) -> None:
        """Argon2id is the hybrid variant and the one to use here.

        Argon2i is side-channel resistant but slower to verify; argon2d is fast
        to verify but GPU friendly. An accidental downgrade is a real risk when
        the algorithm is a constructor argument, so it is asserted.
        """
        assert hash_password(GOOD).startswith("$argon2id$")

    def test_cost_parameters_are_recorded_in_the_hash(self) -> None:
        """Parameters travel with the hash.

        Without this, raising the configured cost would invalidate every stored
        password instead of only new ones, and there would be no way to know what
        cost a given row used.
        """
        stored = hash_password(GOOD)
        assert "m=65536" in stored
        assert "t=3" in stored
        assert "p=4" in stored


class TestVerification:
    def test_malformed_hash_is_a_failed_login_not_a_crash(self) -> None:
        """A broken row must not become a 500.

        Someone reaching this state has already lost the credential; the honest
        outcome is "this does not work". Raising would look like an attack in
        the logs and hide a data problem behind an incident.
        """
        assert verify_password("not-a-hash", GOOD) is False
        assert verify_password("", GOOD) is False

    def test_hash_of_another_algorithm_is_rejected(self) -> None:
        """A bcrypt row, say, from an older scheme.

        Verification must fail rather than accept it: silently treating an
        unsupported format as a match would be a bypass.
        """
        assert verify_password("$2b$12$" + "x" * 53, GOOD) is False

    def test_fresh_hash_needs_no_rehash(self) -> None:
        assert not needs_rehash(hash_password(GOOD))

    def test_weak_parameters_are_flagged_for_rehash(self) -> None:
        """A hash made with deliberately cheap parameters must be upgraded.

        This is the mechanism that lets cost parameters be raised later without
        asking anyone to reset a password.
        """
        cheap = "$argon2id$v=19$m=1024,t=1,p=1$c29tZXNhbHRzb21lc2E$" + "a" * 43
        assert needs_rehash(cheap)

    def test_malformed_hash_is_flagged_for_rehash(self) -> None:
        assert needs_rehash("garbage")


class TestPolicy:
    def test_short_password_rejected(self) -> None:
        with pytest.raises(PasswordPolicyError, match="at least"):
            hash_password("short")

    def test_long_passphrase_accepted(self) -> None:
        """No composition rule.

        A 60-character passphrase with no digits or symbols passes. A policy
        demanding one of each produces `Password1!` far more often than it
        produces something memorable.
        """
        assert verify_password(hash_password("a" * 60), "a" * 60)

    def test_surrounding_whitespace_rejected(self) -> None:
        """A pasted trailing space would silently change the password.

        Trailing whitespace is invisible in a password field, so a user who
        pastes one can never reproduce it later. Reject it at the boundary
        instead of storing an unusable credential.
        """
        with pytest.raises(PasswordPolicyError, match="whitespace"):
            validate_password("  " + GOOD + "  ")

    def test_absurd_length_rejected(self) -> None:
        """Bounds the cost of an Argon2 call driven by a huge request body."""
        with pytest.raises(PasswordPolicyError, match="long"):
            validate_password("a" * 2000)

    def test_exactly_minimum_length_accepted(self) -> None:
        validate_password("a" * 12)


class TestTimingEqualization:
    def test_dummy_verify_does_not_raise(self) -> None:
        """It must do the work, not short-circuit.

        If the dummy hash were malformed, argon2 would raise before spending any
        CPU and an unknown address would answer faster than a known one, which is
        exactly the leak `dummy_verify` exists to prevent.
        """
        dummy_verify()

    def test_dummy_verify_is_repeatable(self) -> None:
        for _ in range(3):
            dummy_verify()
