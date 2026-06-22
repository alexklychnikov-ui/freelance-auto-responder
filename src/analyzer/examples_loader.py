from __future__ import annotations

import os
from pathlib import Path


def resolve_examples_dir(examples_dir: str | Path | None = None) -> Path | None:
    if examples_dir is not None and str(examples_dir).strip():
        return Path(examples_dir)
    env = os.environ.get("RESPONSE_EXAMPLES_DIR", "").strip()
    if env:
        return Path(env)
    return None


def load_response_examples(
    examples_dir: str | Path | None = None,
    *,
    max_files: int = 3,
    max_chars_per_file: int = 2000,
) -> str:
    directory = resolve_examples_dir(examples_dir)
    if directory is None or not directory.is_dir():
        return ""

    snippets: list[str] = []
    for path in sorted(directory.glob("*.md"))[:max_files]:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            snippets.append(text[:max_chars_per_file])
    return "\n\n---\n\n".join(snippets)
