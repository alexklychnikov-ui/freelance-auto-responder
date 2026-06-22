from __future__ import annotations

from pathlib import Path

import pytest

from src.store.repository import ProjectRepository


@pytest.fixture
def repo(tmp_path: Path) -> ProjectRepository:
    return ProjectRepository(tmp_path / "test.db")


def test_is_known_false_for_new(repo: ProjectRepository) -> None:
    assert repo.is_known("kwork", "kwork_dev_it", "3201949") is False


def test_insert_new_and_is_known(repo: ProjectRepository) -> None:
    repo.insert_new(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3201949",
        title="Test project",
        url="https://kwork.ru/projects/3201949",
    )
    assert repo.is_known("kwork", "kwork_dev_it", "3201949") is True


def test_bootstrap_skip_marks_skipped(repo: ProjectRepository) -> None:
    repo.bootstrap_skip(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3201948",
        title="Bootstrap",
    )
    assert repo.is_known("kwork", "kwork_dev_it", "3201948") is True


def test_update_status(repo: ProjectRepository) -> None:
    repo.insert_new(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3201949",
        status="new",
    )
    repo.update_status("kwork", "kwork_dev_it", "3201949", "scored", fit=True, score=8.0)


def test_scan_state_roundtrip(repo: ProjectRepository) -> None:
    assert repo.get_scan_state("kwork_dev_it") is None
    repo.set_scan_state(
        source_key="kwork_dev_it",
        platform="kwork",
        last_known_project_id="3201949",
    )
    state = repo.get_scan_state("kwork_dev_it")
    assert state is not None
    assert state.platform == "kwork"
    assert state.last_known_project_id == "3201949"


def test_consecutive_known_count(repo: ProjectRepository) -> None:
    for pid in ("3201950", "3201949", "3201948"):
        repo.insert_new(
            platform="kwork",
            source_key="kwork_dev_it",
            project_id=pid,
        )
    count = repo.count_consecutive_known_from_top(
        "kwork",
        "kwork_dev_it",
        ["3201950", "3201949", "3201947"],
    )
    assert count == 2
