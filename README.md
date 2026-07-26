# UGC RADAR bot (@ugc_radarbot)

Telegram-бот для Packman Production: регистрация UGC-креаторов, запросы брендов, отклики, аналитика, клиентские мини-проекты.

Local AI-sovereign workspace. The model can be in the cloud, but the project memory lives here.

## Start here

1. Read `AGENTS.md` (canonical instructions for agents)
2. Read `docs/PROJECT_CONTEXT.md` (what this project is)
3. Read `docs/RECOVERY.md` to restore context with a new agent/account

## Daily flow

`prompts/start-session.md` → work → `prompts/end-session.md` → git commit

## Quick reference

| What | Where |
|------|-------|
| Agent instructions | `AGENTS.md` |
| Project context | `docs/PROJECT_CONTEXT.md` |
| Decisions log | `docs/DECISIONS.md` |
| Changelog | `docs/CHANGELOG.md` |
| Recovery guide | `docs/RECOVERY.md` |
| Session logs | `logs/sessions/` |
| Session prompts | `prompts/` |
| Full legacy handoff | `HANDOFF.md` |
| Full agent log | `AGENT_LOG.md` |
| Deploy instructions | `DEPLOY.md` |
| Community features | `PROJECT_MAP_community.md` |

## Deploy

```bash
bash check.sh    # smoke test
railway up       # deploy to Railway
```
