---
name: subagent-team
description: Определяет роли команды субагентов и шаблоны задач для coder, qa, devops, security, simplify, efficiency и опциональных ролей. Использовать при делегировании реализации.
---

# Subagent Team

## Базовые роли

- `teamlead`: оркестрация, гейты этапов, приоритезация.
- `coder`: реализация и паттерны.
- `qa`: тестирование и отчет о качестве.
- `devops`: docker, инфраструктура, сервисы.
- `security-expert`: оценка безопасности и угроз.
- `code-simplify-assessor`: простота, чистота, лаконичность.
- `efficiency-assessor`: производительность и ресурсы.

## Опциональные роли

- `designer`, `manual-tester`, `technical-writer`, `compliance-officer`.

## Контракт делегирования

Каждому субагенту передавать:

1. цель шага;
2. границы изменений;
3. критерии приемки;
4. ограничения (архитектура, безопасность, этап);
5. требуемый формат отчета.

## Формат отчета субагента

```text
role: <name>
task: <short>
result: <done|partial|blocked>
changes: <files/artifacts>
checks: <passed/failed + evidence>
risks: <list>
handoff: <next role or next action>
```
