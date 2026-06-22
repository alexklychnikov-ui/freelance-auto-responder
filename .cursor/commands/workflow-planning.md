# Этап: planning

Режим **планирование**. Запрещено писать код.

Предусловие: `design` закрыт.

Соблюдай `.cursor/rules/planning-phase-gates.mdc` и policy для этапа `planning` в `ai/workflow/stage-policy.json`.

Сформируй пошаговый план: для каждого шага — цель, входы, действия, проверки, ожидаемый результат. Шаги должны быть верифицируемыми; для рискованных операций — rollback/contingency.

План и QA/верификация: используй/обновляй артефакты под `ai/workflow/planning/` (например `implementation-plan.json`, `qa-test-plan.json`, `review-gates.json`), согласуясь с уже существующей схемой файлов.

Checkpoint в `logs/agent-worklog.txt` после закрытия этапа.
