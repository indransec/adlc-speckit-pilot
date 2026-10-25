"""Local-disk object store (stands in for S3 / blob storage). Keys are POSIX-style paths."""
from __future__ import annotations

from pathlib import Path

from shared.config import object_store_dir


def path(key: str) -> Path:
    p = (object_store_dir() / key).resolve()
    if object_store_dir().resolve() not in p.parents:
        raise ValueError(f"key escapes object store: {key}")
    return p


def put(key: str, data: bytes) -> str:
    p = path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return key


def get(key: str) -> bytes:
    return path(key).read_bytes()


def exists(key: str) -> bool:
    return path(key).exists()
