# AGENTS.md - Project Instructions

This file is the source of truth for AI coding agents working in this project.
Read it before making changes. If a user request conflicts with this file, stop and ask.

## Project

- **Name:** UGC RADAR bot (@ugc_radarbot)
- **Goal:** Telegram-бот для Packman Production — регистрация UGC-креаторов, запросы брендов, отклики, аналитика, клиентские мини-проекты (Сингл, PapKids)
- **Audience:** UGC-креаторы (исполнители), админы Packman (Игорь Попов)
- **Current phase:** production
- **Stack:** Python 3.11, aiogram 3, PostgreSQL (Railway), Google Sheets + Apps Script, Railway deploy

## Source of Truth

- Project context: `docs/PROJECT_CONTEXT.md`
- Decisions: `docs/DECISIONS.md`
- Change history: `docs/CHANGELOG.md`
- Session logs: `logs/sessions/`
- Recovery instructions: `docs/RECOVERY.md`
- Legacy handoff (полный): `HANDOFF.md`
- Legacy agent log (история): `AGENT_LOG.md`
- Deploy instructions: `DEPLOY.md`
- Community features map: `PROJECT_MAP_community.md`

Do not rely on chat history as the only memory of the project.
If something matters, write it to a project file.

## Working Rules

- Before work: read this file, `docs/PROJECT_CONTEXT.md`, latest session log, and `docs/DECISIONS.md`.
- During work: keep changes scoped to the current task.
- After work: update session log, changelog, and decisions if relevant.
- Never delete files without explicit user approval.
- Never expose secrets, tokens, API keys, private credentials, or `.env` values.
- Ask before publishing, sending, deploying, deleting, or sharing anything externally.
- Deploy only via `railway up` (CLI) or GitHub push to `popovigory/ugc-bot-v2`. NEVER push to `LubeznyGospodin/ugc-bot` — this is a dead-end repo.
- Real broadcasts to creators — ONLY after explicit confirmation. Test on Игорь's chat_id.
- EnsembleData API: 50 units/day limit. No manual `reach_run` or test calls. Only automated 10:00 MSK run.
- Local smoke tests: use `.venv` (Python 3.11), system python3.9 fails on type hints.

## Logging Rules

Every completed task must leave a local trace.

Update `logs/sessions/YYYY-MM-DD-<topic>.md` with:
- user request
- files inspected
- actions performed
- commands run
- files created or changed
- decisions made
- open questions
- next steps

Update `docs/DECISIONS.md` when:
- architecture changes
- tool choice changes
- file structure changes
- a tradeoff is accepted
- a risky shortcut is chosen

Update `docs/CHANGELOG.md` when:
- user-visible behavior changes
- files are created
- configuration changes
- prompts or workflows change

## File Zones

- `bot/` — source code (handlers, models, sync, sheets, config, etc.)
- `call_control/` — pipeline контроля звонков B24 → whisper → Claude → Telegram
- `outreach/` — Instagram DM outreach workflow
- `apps_script/` — Google Apps Script source (clasp); contains webhook SECRET — excluded from git
- `data/` — runtime data (excluded from git)
- `docs/` — project memory and documentation
- `logs/` — session and action logs
- `prompts/` — reusable prompts for session management
- `outputs/` — generated deliverables
- `graphify-out/` — knowledge graph output (generated)

## Report Format

At the end of each task, report:
1. What was done.
2. Which files changed.
3. Which checks passed.
4. What still needs attention.
5. What the next agent should do.
