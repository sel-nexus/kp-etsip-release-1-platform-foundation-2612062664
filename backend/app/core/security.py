"""Implement password hashing and signed session tokens."""

from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt


def hash_password(password: str) -> str:
    """Hash a validated password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password without exposing malformed hashes or oversized inputs."""
    try:
        encoded = password.encode("utf-8")
        if len(encoded) > 72:
            return False
        return bcrypt.checkpw(encoded, password_hash.encode("ascii"))
    except (ValueError, UnicodeError):
        return False


def create_token(user: dict, secret: str) -> str:
    """Sign an eight-hour token tied to the user's session version."""
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user["_id"]), "userId": str(user["_id"]), "role": user["role"], "ver": user.get("tokenVersion", 0), "iat": now, "exp": now + timedelta(hours=8)}, secret, algorithm="HS256")


def decode_token(token: str, secret: str) -> dict:
    """Verify a token's signature and expiry.

    Raises:
        JWTError: When the token is invalid or expired.
    """
    return jwt.decode(token, secret, algorithms=["HS256"], options={"require_exp": True, "require_sub": True})
