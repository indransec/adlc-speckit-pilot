"""Central configuration: repo paths, manifests and environment.

Paths that hold runtime data are functions (not constants) so evals and tests can
point DATA_DIR at an isolated directory before anything touches the database.

A simulation run (APP_ENV=sim, see sim/workspace.py) also sets PROJECT_ROOT to its own
workspace copy of the governed project content (system.yaml, docs, prompts, routing rules)
and EVIDENCE_DIR to its own evidence store, so the repo's files are never modified.
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


def project_root() -> Path:
    """Governed project content: the repo itself, or a simulation run's workspace."""
    return Path(os.environ.get("PROJECT_ROOT") or ROOT)


def evidence_root() -> Path:
    return Path(os.environ.get("EVIDENCE_DIR") or ROOT / "evidence")


def app_env() -> str:
    return os.environ.get("APP_ENV", "dev")


def is_sim() -> bool:
    return app_env() == "sim"


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
def _yaml_at(path: str, mtime: float):
    with open(path) as f:
        return yaml.safe_load(f)


def system_manifest() -> dict:
    """Re-read when the file changes: a simulation run cuts new releases into its workspace."""
    p = project_root() / "system.yaml"
    return _yaml_at(str(p), p.stat().st_mtime)


@lru_cache
def _env_config(env: str) -> dict:
    return load_yaml(f"deploy/environments/{env}.yaml")


def env_config() -> dict:
    return _env_config(app_env())


@lru_cache
def model_catalog() -> dict:
    return {m["id"]: m for m in load_yaml("deploy/models.yaml")["models"]}


def release() -> str:
    return system_manifest()["release"]


def system_id() -> str:
    return system_manifest()["id"]
