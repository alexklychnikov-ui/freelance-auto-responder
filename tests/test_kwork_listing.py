from __future__ import annotations

from copy import deepcopy

from deploy.kwork_listing_fullcrm import CRM, buyer_copy_problems
from deploy.kwork_listing_landing import LANDING
from deploy.kwork_listing_price_monitor import PRICE
from deploy.kwork_listing_skel import NEW, assemble, validate


def test_fullcrm_assemble_validate_ok() -> None:
    built = assemble(CRM)
    assert built["github_url"] == "https://github.com/alexklychnikov-ui/FullCRM"
    assert validate(built) == []
    assert buyer_copy_problems(built) == []


def test_landing_assemble_validate_ok() -> None:
    built = assemble(LANDING)
    assert built["github_url"] == "https://github.com/alexklychnikov-ui/MyPortfolio"
    assert validate(built) == []


def test_price_assemble_validate_ok() -> None:
    built = assemble(PRICE)
    assert built["github_url"] == "https://github.com/alexklychnikov-ui/PriceMonitoring"
    assert validate(built) == []


def test_new_without_github_url_skips_github_case() -> None:
    built = assemble(NEW)
    assert built["github_url"] == ""
    problems = validate(built)
    assert problems == []
    assert not any("github case" in item for item in problems)


def test_landing_price_buyer_5000_fails_github_case() -> None:
    src = deepcopy(LANDING)
    src["price_buyer"] = 5000
    problems = validate(assemble(src))
    assert any("github case" in item and "price_buyer" in item for item in problems)


def test_landing_extras_slice_fails_github_case() -> None:
    src = deepcopy(LANDING)
    src["extras"] = src["extras"][:2]
    problems = validate(assemble(src))
    assert any("github case" in item and "extras" in item for item in problems)


def test_extra_price_500_fails_github_case() -> None:
    src = deepcopy(LANDING)
    src["extras"][0]["price"] = 500
    problems = validate(assemble(src))
    assert any("github case" in item and "extra[0].price" in item for item in problems)


def test_five_extras_all_800_sum_4000_ok() -> None:
    src = deepcopy(LANDING)
    src["extras"] = [{**extra, "price": 800} for extra in src["extras"][:5]]
    assert validate(assemble(src)) == []


def test_github_url_without_github_com_does_not_trigger() -> None:
    src = deepcopy(LANDING)
    src["github_url"] = "https://alexklyvibe.ru"
    src["price_buyer"] = 5000
    problems = validate(assemble(src))
    assert not any("github case" in item for item in problems)
