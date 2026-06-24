"""Сроки выполнения, доступные в dropdown формы отклика Kwork."""
from __future__ import annotations

KWORK_DELIVERY_DAY_OPTIONS: tuple[int, ...] = (
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    10,
    14,
    21,
    30,
    60,
)


def snap_delivery_days(days: int) -> int:
    """Ближайший допустимый срок из списка Kwork (при равной дистанции — меньший)."""
    value = max(1, min(60, int(days)))
    return min(KWORK_DELIVERY_DAY_OPTIONS, key=lambda d: (abs(d - value), d))
