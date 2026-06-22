from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.models import ProjectFull, ProjectPreview, ReplyEvent, SubmitResult


@runtime_checkable
class PlatformAdapter(Protocol):
    platform_id: str
    source_key: str

    def scan_new(self) -> list[ProjectPreview]: ...

    def read_full(self, project_id: str) -> ProjectFull: ...

    def submit_response(
        self, project_id: str, text: str, price: str | None
    ) -> SubmitResult: ...

    def monitor_replies(self) -> list[ReplyEvent]: ...
