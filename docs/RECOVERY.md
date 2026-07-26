# Recovery Guide

Use this file when opening the project with a new AI account, new agent, or after losing chat history.

## Recovery Order

1. Read `AGENTS.md`
2. Read `docs/PROJECT_CONTEXT.md`
3. Read `docs/DECISIONS.md`
4. Read the latest 3 files in `logs/sessions/`
5. Read `HANDOFF.md` (legacy full handoff — detailed architecture)
6. Read `AGENT_LOG.md` first 100 lines (legacy agent log — history of all changes)
7. Run `git status` and `git log --oneline -10`
8. Summarize current state before changing anything

## What To Reconstruct

- What the project is (Telegram bot @ugc_radarbot for Packman Production)
- What has been done (see CHANGELOG.md and git log)
- Which decisions matter (see DECISIONS.md)
- Which files are source of truth (AGENTS.md lists them)
- What is risky (deploy, broadcasts, reach API limits)
- What the next step should be

## Project-Specific Recovery Notes

### Environment Setup
```bash
# Activate venv (Python 3.11 required)
source .venv/bin/activate

# Check .env exists (copy from .env.example if missing)
ls .env

# Smoke test
bash check.sh
```

### Critical Secrets (values NOT stored here)
- `BOT_TOKEN` — Telegram bot token
- `DATABASE_URL` — PostgreSQL connection string (Railway)
- `SHEETS_WEBHOOK_URL` — Apps Script web-app URL
- `SHEETS_WEBHOOK_SECRET` — shared secret for webhook auth
- `ADMIN_IDS` — comma-separated Telegram user IDs

### Deploy
```bash
# From local machine with Railway CLI
cd ~/Desktop/ugc_bot_v2
bash check.sh    # smoke test first!
railway up       # deploy to Railway
```

### Git Remote
- Origin: `popovigory/ugc-bot-v2` (private)
- NEVER push to `LubeznyGospodin/ugc-bot` (dead-end repo)

### Google Sheets
- Table ID: `1Hoqi-lLxyJtApNR6WGN59Rwz0u0hLPqFuDE-fQbzYyI`
- Apps Script project: `1GZBFC5V7icrhHFxVgtshnCxl9M9JfIZxtdpp0w-ZckGV75FaqQBbd2gI`
- Deploy Apps Script via clasp (`clasp push -i DEPLOY_ID`)

### Background Processes
- `bot/sync.py` — sheets → DB every 120s
- `bot/nudge.py` — nudge unfinished registrations after 2h
- `bot/single.py` — Сингл deadlines + admin reports
- `bot/ops.py` — health ping + payment reminders
- `bot/reach.py` — reach collection once/day 10:00 MSK (50 units/day limit!)
- `call_control/pipeline.py` — B24 call analysis every 15 min (separate cron)

## Recovery Prompt

Paste this into a new agent:

> Read AGENTS.md, docs/PROJECT_CONTEXT.md, docs/DECISIONS.md, and the latest session logs in logs/sessions/. Then reconstruct:
> 1. current project state
> 2. last completed work
> 3. open risks
> 4. next recommended step
>
> Do not edit files until you finish the reconstruction.
