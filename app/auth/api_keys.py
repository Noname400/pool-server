"""
app/auth/api_keys.py — API key generation and hashing.

Uses bcrypt for key hashing (salted, GPU-resistant).
Supports legacy SHA256 hashes for backward compatibility during migration.
"""
import hashlib
import secrets

import bcrypt


def generate_api_key() -> tuple[str, str]:
    parts = [secrets.token_hex(4) for _ in range(4)]
    plaintext = "-".join(parts)
    return plaintext, hash_api_key(plaintext)


def hash_api_key(key: str) -> str:
    return bcrypt.hashpw(key.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_api_key(key: str, stored_hash: str) -> bool:
    if stored_hash.startswith("$2"):
        return bcrypt.checkpw(key.encode("utf-8"), stored_hash.encode("utf-8"))
    return hashlib.sha256(key.encode("utf-8")).hexdigest() == stored_hash
