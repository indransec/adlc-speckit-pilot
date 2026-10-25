"""Central configuration: repo paths, manifests and environment.

Paths that hold runtime data are functions (not constants) so evals and tests can
point DATA_DIR at an isolated directory before anything touches the database.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_CACHE = ROOT / ".cache" / "fastembed"


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR") or ROOT / "data")


def db_path() -> Path:
    return data_dir() / "claims.db"


def checkpoint_db_path() -> Path:
    return data_dir() / "checkpoints.db"


def object_store_dir() -> Path:
    return data_dir() / "object_store"


def faiss_dir() -> Path:
    return data_dir() / "faiss"


def app_env() -> str:
    return os.environ.get("APP_ENV", "dev")


def openrouter_api_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or None


def approval_token_secret() -> str:
    return os.environ.get("APPROVAL_TOKEN_SECRET", "dev-only-approval-secret-change-me-32b")


def load_yaml(rel_or_abs: str | Path):
    p = Path(rel_or_abs)
    p = p if p.is_absolute() else ROOT / p
    with open(p) as f:
        return yaml.safe_load(f)


@lru_cache
def system_manifest() -> dict:
    return load_yaml("system.yaml")


@lru_cache
def env_config() -> dict:
    return load_yaml(f"deploy/environments/{app_env()}.yaml")


@lru_cache
def model_catalog() -> dict:
    return {m["id"]: m for m in load_yaml("deploy/models.yaml")["models"]}


def release() -> str:
    return system_manifest()["release"]


def system_id() -> str:
    return system_manifest()["id"]
