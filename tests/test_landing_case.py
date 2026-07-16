from __future__ import annotations

from src.analyzer.landing_case import (
    is_landing_project,
    landing_scoring_context,
)
from src.analyzer.lightrag_client import LightRagClient
from src.models import ProjectFull


def test_is_landing_project_matches_one_page_site() -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3215930",
        url="https://kwork.ru/projects/3215930/view",
        title="Создать одностраничный сайт по ТЗ за 7 дней",
        full_description="Нужен лендинг для услуг, адаптив, форма заявки",
    )
    assert is_landing_project(project)


def test_is_landing_project_rejects_parsing() -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1/view",
        title="Парсинг каталога",
        full_description="Нужен парсер Python",
    )
    assert not is_landing_project(project)


def test_landing_scoring_context_mentions_myportfolio() -> None:
    text = landing_scoring_context()
    assert "MyPortfolio" in text
    assert "Next.js" in text


def test_lightrag_injects_landing_case() -> None:
    client = LightRagClient(search_fn=lambda q, m: "")
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3215930",
        url="https://kwork.ru/projects/3215930/view",
        title="Создать одностраничный сайт по ТЗ",
        full_description="Лендинг",
    )
    context = client.get_scoring_context(project)
    assert "Подтверждённый кейс: лендинг" in context
    assert "MyPortfolio" in context
