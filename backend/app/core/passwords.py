"""Argon2id password hashing for the HR credential surface.

Parameters follow the OWASP 2024 floor (time_cost=3, 64 MiB, parallelism=4).
The PHC string embeds the algorithm + params, so raising cost later still
verifies old hashes and `needs_rehash` flags them for opportunistic upgrade on
the next successful login.

`dummy_verify` exists to keep the failed-login timing profile constant whether
or not a user row was found — otherwise a fast "no such user" path leaks
account existence via response time.
"""
from __future__ import annotations

import math

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

MIN_LENGTH = 12

# 64 MiB = 65536 KiB.
_hasher = PasswordHasher(
    time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16
)

# Precomputed once at import so `dummy_verify` costs the same as a real verify.
_DUMMY_HASH = _hasher.hash("inspro-nonexistent-account-placeholder")


def hash_password(plaintext: str) -> str:
    """Return an Argon2id PHC string for `plaintext`."""
    return _hasher.hash(plaintext)


def verify_password(password_hash: str, plaintext: str) -> bool:
    """True iff `plaintext` matches `password_hash`. Never raises on mismatch."""
    try:
        _hasher.verify(password_hash, plaintext)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True if the stored hash was made with weaker params than the current policy."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def dummy_verify(plaintext: str) -> None:
    """Burn a verify against a fixed hash so the no-user path isn't faster."""
    try:
        _hasher.verify(_DUMMY_HASH, plaintext)
    except (VerifyMismatchError, InvalidHashError):
        pass


def estimate_entropy_bits(password: str) -> float:
    """Rough Shannon estimate: len * log2(character-pool size).

    Not a substitute for zxcvbn's dictionary analysis, but enough for a floor
    that rejects short/single-class passwords. Distinct-character shrinkage
    penalises repetition ("aaaaaaaaaaaa").
    """
    if not password:
        return 0.0
    pool = 0
    if any(c.islower() for c in password):
        pool += 26
    if any(c.isupper() for c in password):
        pool += 26
    if any(c.isdigit() for c in password):
        pool += 10
    if any(not c.isalnum() for c in password):
        pool += 32
    pool = max(pool, 1)
    effective_len = min(len(password), len(set(password)) * 2)
    return effective_len * math.log2(pool)


def password_meets_policy(password: str, min_entropy_bits: int) -> tuple[bool, str]:
    """(ok, reason). Enforces the length floor + entropy floor."""
    if len(password) < MIN_LENGTH:
        return False, f"Password must be at least {MIN_LENGTH} characters."
    if estimate_entropy_bits(password) < min_entropy_bits:
        return False, "Password is too weak — add length or mix character types."
    return True, ""
