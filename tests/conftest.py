"""Shared pytest fixtures.

Two goals:
  1. Keep tests OFF the real `data/` directory so a test run never
     pollutes your actual flywheel / traces / vectorstore.
  2. Provide a stub agent_invoke so eval-runner tests don't hit the LLM.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _isolate_data_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect every settings path to a tmp dir for the duration of each test.

    We patch the already-instantiated `settings` object's attributes
    (pydantic-settings objects allow attribute assignment by default).
    This is the single most important fixture in the suite: without it,
    running `pytest` would append bogus records to data/flywheel/*.jsonl
    and write traces into data/traces/.
    """
    from config import settings

    tmp_data = tmp_path / "data"
    for sub in [
        "vectorstore", "traces",
        "eval/results", "eval/dataset",
        "flywheel", "post_training",
    ]:
        (tmp_data / sub).mkdir(parents=True, exist_ok=True)

    settings.vector_store_dir = tmp_data / "vectorstore"
    settings.eval_output_dir = tmp_data / "eval" / "results"
    settings.badcase_store_path = tmp_data / "flywheel" / "badcases.jsonl"
    settings.goodcase_store_path = tmp_data / "flywheel" / "goodcases.jsonl"
    settings.post_train_output_dir = tmp_data / "post_training"

    # Make sure the tmp files exist (JsonlStore expects to .touch() them,
    # but other code may read before write).
    settings.badcase_store_path.touch()
    settings.goodcase_store_path.touch()


@pytest.fixture()
def stub_agent_invoke() -> callable:
    """Return a deterministic fake invoke for eval-runner tests.

    Usage:
        def test_x(stub_agent_invoke):
            runner = EvalRunner(agent_invoke=stub_agent_invoke)
            ...
    """
    def _invoke(user_input: str) -> str:
        # Echo a canned answer so metrics can be tested deterministically.
        if "12 * 7" in user_input:
            return "84"
        if "sqrt" in user_input:
            return "15"
        return f"echo: {user_input}"
    return _invoke
