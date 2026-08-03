"""Encryption for OAuth tokens held at rest.

A Google refresh token is a long-lived key to somebody's mailbox. In a multi-tenant
product a database dump must not hand over every customer's mail, so tokens are
encrypted with a key that lives in the environment rather than the database.

Fails closed: if OAuth is enabled without a key configured, the connect flow refuses to
start rather than quietly storing tokens in plaintext.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class TokenEncryptionUnavailable(RuntimeError):
    """Raised when tokens would have to be stored without protection."""


@lru_cache
def _cipher() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise TokenEncryptionUnavailable(
            "TOKEN_ENCRYPTION_KEY is not set, so OAuth refresh tokens cannot be "
            "stored safely. Generate one with: python -c \"from cryptography.fernet "
            "import Fernet; print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise TokenEncryptionUnavailable(
            "TOKEN_ENCRYPTION_KEY is not a valid Fernet key."
        ) from exc


def encrypt(value: str) -> str:
    return _cipher().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Decrypt a stored token.

    A rotated or mistyped key surfaces as a clear failure rather than a corrupted
    token that produces a confusing 401 from Google much later.
    """
    try:
        return _cipher().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise TokenEncryptionUnavailable(
            "Stored token could not be decrypted. TOKEN_ENCRYPTION_KEY has most "
            "likely changed; affected users must reconnect their mailbox."
        ) from exc
