from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "evidence"

REQUIRED_FIXTURES = (
    "3252339_torgi_rossii_lot.html",
    "3252339_torgi_gov_public.html",
    "3252339_bad_template_response.txt",
)


def test_golden_case_fixtures_exist() -> None:
    for name in REQUIRED_FIXTURES:
        path = FIXTURES / name
        assert path.is_file(), f"missing fixture: {path}"
        assert path.stat().st_size > 0, f"empty fixture: {path}"


@pytest.mark.evidence_baseline
def test_evidence_collector_module_contract() -> None:
    """Contract flip: pass once src.evidence.collector exists (later stages).

    Excluded from default suite via pytest.ini addopts `-m "not evidence_baseline"`.
    Run gaps (expect red until implemented):
      pytest -o addopts= -m evidence_baseline
    """
    spec = importlib.util.find_spec("src.evidence.collector")
    assert spec is not None, (
        "baseline gap: src.evidence.collector is missing; "
        "TZ URLs are not researched yet"
    )


@pytest.mark.evidence_baseline
def test_orchestrator_ensure_evidence_contract() -> None:
    """Contract flip: pass once orchestrator exposes ensure_evidence.

    Excluded from default suite via pytest.ini addopts `-m "not evidence_baseline"`.
    Run:
      pytest -o addopts= -m evidence_baseline
    """
    from src.pipeline.orchestrator import PipelineOrchestrator

    assert hasattr(PipelineOrchestrator, "ensure_evidence"), (
        "baseline gap: orchestrator has no ensure_evidence; "
        "evidence step is not wired into prepare flow"
    )
    assert callable(getattr(PipelineOrchestrator, "ensure_evidence"))

