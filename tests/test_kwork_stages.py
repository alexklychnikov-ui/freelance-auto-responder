from __future__ import annotations

from src.adapters.kwork_stages import plan_offer_stages
from src.models import ProjectFull


def _project(title: str, desc: str = "") -> ProjectFull:
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title=title,
        full_description=desc,
    )


def test_plan_two_stages_default() -> None:
    stages = plan_offer_stages(10000, _project("Сайт", "лендинг на wordpress"))
    assert len(stages) == 2
    assert sum(a for _, a in stages) == 10000
    assert all(a >= 500 for _, a in stages)


def test_plan_three_stages_from_title_only() -> None:
    project = _project("Доработать 2 Telegram-бота по готовому ТЗ", "")
    stages = plan_offer_stages(35000, project)
    assert len(stages) == 3
    assert sum(a for _, a in stages) == 35000
    project = _project(
        "Доработать 2 Telegram-бота",
        "Проект №1: бот учёта контактов. Проект №2: клиентский бот поддержки.",
    )
    stages = plan_offer_stages(35000, project)
    assert len(stages) == 3
    assert sum(a for _, a in stages) == 35000
    assert "контакт" in stages[1][0].lower()
    assert "поддерж" in stages[2][0].lower()
