# Team workflow: research → design → planning → coding

Ты ведёшь задачу **строго по этапам** без пропусков. Соблюдай project rules (`.cursor/rules/`), в частности `workflow-stage-gates`, `research-phase-constraints`, `design-phase-deliverables`, `planning-phase-gates`, `coding-orchestration`, и применимые guardrails.

**Немедленно прочитай и следуй** workspace skills:
- `.cursor/skills/team-orchestrator/SKILL.md`
- `.cursor/skills/subagent-team/SKILL.md`

Политика этапов (machine-readable): `ai/workflow/stage-policy.json`. Роли и шаблоны: `ai/subagents/roles.json`, `ai/subagents/task-templates.json`.

## Вход от пользователя

В начале диалога пользователь описывает цель задачи. Если цель/scope/ограничения неясны — **остановись и задай уточняющие вопросы** до старта `research`.

## Протокол

1. **research** — только факты из кода/доков/ввода; без кода; без предложения решений; зафиксируй неоднозначности.
   - Если нужны пользовательские данные/знания, делай поиск через LightRAG и используй материалы из `C:\Python\Projects\LigthRAG` как дополнительный источник подтверждаемых фактов.
2. **design** — `C4`, `DFD`, `Sequence`; по контексту API contract / migration / security / NFR; явные допущения и открытые вопросы.
3. **planning** — пошаговый план с критериями приёмки, проверками, rollback для рисковых шагов; без кода.
4. **coding** — главный агент **оркестратор**: делегируй профильным субагентам (coder/qa/devops/security/…); сам не пиши объёмный код; после шагов — ревью; findings уровня middle+ возвращай на доработку.

Артефакты по проекту складывай под `ai/workflow/` (design/planning/coding), не выдумывай пути — согласуй с существующей структурой репозитория.

## Checkpoint

После **каждого** закрытого этапа добавь версионную запись в `logs/agent-worklog.txt`: дата/время, краткий prompt, что сделано, итог, версия bump по смыслу.

## Старт

Определи текущий этап (если продолжение — не перезапускай закрытые этапы без явного запроса). Начни с `research`, если этап не задан.
