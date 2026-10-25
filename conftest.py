"""Test bootstrap: isolated data dir, deterministic (mock) LLM, no telemetry export."""
import itertools
import os
import random
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="adlc-tests-")
os.environ["TELEMETRY_ENABLED"] = "false"
os.environ.pop("OPENROUTER_API_KEY", None)

import pytest  # noqa: E402

_ids = itertools.count(1)


@pytest.fixture(scope="session")
def platform():
    from backend import service
    from synthetic.generate import setup_platform
    setup_platform()
    service.bootstrap()
    return service


@pytest.fixture
def make_claim(platform):
    from synthetic.generate import used_flights
    from synthetic.materialize import materialize
    from synthetic.scenarios import build_case

    def _make(scenario: str, seed: int = 1) -> str:
        cid = f"CLM-TEST-{next(_ids):04d}"
        return materialize(build_case(scenario, random.Random(seed), cid, used_flights=used_flights()))
    return _make


@pytest.fixture
def runtime_reset(platform):
    from shared.runtime import reset_runtime
    reset_runtime()
    yield
    reset_runtime()
