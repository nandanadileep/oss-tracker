import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.config import Config  # noqa: E402
from harness.events import Ledger  # noqa: E402


@pytest.fixture
def cfg():
    return Config()


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.jsonl", run_id="test")
