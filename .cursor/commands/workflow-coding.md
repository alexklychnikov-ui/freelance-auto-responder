# Этап: coding

Режим **реализация через оркестрацию**. Главный агент не заменяет собой все роли: делегируй профильным субагентам согласно `.cursor/rules/coding-orchestration.mdc` и `.cursor/skills/subagent-team/SKILL.md`.

Предусловие: `planning` закрыт.

Правила:
- Назначай задачи только профильным исполнителям (coder, qa, devops, security, simplify, efficiency и т.д.).
- После каждого значимого шага — проверка качества и командное ревью по `ai/workflow/planning/review-gates.json` (если актуально).
- Замечания уровня middle+ обязательно возвращай исполнителю на доработку.
- Фиксируй отчёты/checkpoint под `ai/workflow/coding/` (`stage_checkpoint.json`, `team_review.json`, `subagent_reports.json` — как принято в репозитории).

Не обходи обязательные проверки ради скорости.

Checkpoint в `logs/agent-worklog.txt` после значимых шагов и по закрытию этапа.
