"""
Синк с группой «Креаторы Packman Prod» (-1004313489669): читает историю (Telethon),
опознаёт креатора по `id <tg_id>` в тексте поста, и ставит 👍 на посты тех, кто УЖЕ
залит на сайт. Учёт в group_published.json (реестр против дублей).

Посты бывают двух типов:
  📷 Фото креатора <Имя> (@user, id <tg_id>)
  🎬 Работы креатора <Имя> (@user, id <tg_id>) — N шт.

Запуск:
  python group_sync.py --dry     # только показать, что нашёл и что бы отметил (без реакций)
  python group_sync.py           # проставить реакции (идемпотентно, дубли пропускает)

Требует data/creators_user.session (сделать разово: python tg_login.py).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl import functions, types

load_dotenv("/Users/nastasyapopova/Desktop/ugc_bot_v2/.env")

GROUP = -1004313489669
LEDGER = "/Users/nastasyapopova/Desktop/ugc_bot_v2/group_published.json"
SITE = "/Users/nastasyapopova/Desktop/ugc_bot_v2/site_published.json"
REACTION = "👍"
DRY = "--dry" in sys.argv

_ID = re.compile(r"id\s+(\d+)")


def _load(path, default):
    return json.load(open(path)) if os.path.exists(path) else default


async def main() -> None:
    site = set(str(x) for x in _load(SITE, []))
    ledger = _load(LEDGER, {})  # {tg: {"photo":true,"works":true}}
    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]

    async with TelegramClient("data/creators_user", api_id, api_hash) as client:
        grp = await client.get_entity(GROUP)
        # tg -> {"photo": msg_id, "works": msg_id, "name": ...}
        found: dict[str, dict] = {}
        async for msg in client.iter_messages(grp, limit=None):
            t = msg.message or ""
            if "креатор" not in t.lower():
                continue
            m = _ID.search(t)
            if not m:
                continue
            tg = m.group(1)
            kind = "photo" if ("Фото" in t or "📷" in t) else ("works" if ("Работ" in t or "🎬" in t) else None)
            if not kind:
                continue
            rec = found.setdefault(tg, {})
            rec.setdefault(kind, msg.id)  # iter новые→старые: берём самый свежий
            name = re.sub(r"\s*\(@.*", "", re.sub(r"^\W*(Фото|Работы) креатора\s*", "", t)).strip()
            rec.setdefault("name", name[:40])

        on_site = {tg: r for tg, r in found.items() if tg in site}
        print(f"постов-креаторов в группе: {len(found)} уник. | из них на сайте: {len(on_site)} "
              f"| отмечено сейчас: {len(ledger)}")

        async def _set_reaction(mid, remove):
            """Ставит/снимает 👍 с обработкой flood-wait. True при успехе."""
            reaction = [] if remove else [types.ReactionEmoji(emoticon=REACTION)]
            for _ in range(3):
                try:
                    await client(functions.messages.SendReactionRequest(
                        peer=grp, msg_id=mid, reaction=reaction))
                    await asyncio.sleep(1.0)
                    return True
                except FloodWaitError as fw:
                    print(f"  ⏳ flood-wait {fw.seconds + 3}s", flush=True)
                    await asyncio.sleep(fw.seconds + 3)
                except Exception as e:  # noqa: BLE001
                    print(f"  ✗ msg {mid}: {str(e)[:70]}")
                    return False
            return False

        added = removed = skipped = 0
        for tg, rec in found.items():
            want = tg in site  # должен быть 👍, только если реально на сайте
            for kind in ("photo", "works"):
                mid = rec.get(kind)
                if not mid:
                    continue
                has = bool(ledger.get(tg, {}).get(kind))
                if want and not has:  # надо поставить
                    if DRY:
                        print(f"  [dry] +👍 {rec.get('name','')} (id {tg}) {kind}")
                        added += 1
                        continue
                    if await _set_reaction(mid, remove=False):
                        ledger.setdefault(tg, {})[kind] = True
                        added += 1
                        print(f"  +👍 {rec.get('name','')} (id {tg}) {kind}", flush=True)
                        json.dump(ledger, open(LEDGER, "w"), ensure_ascii=False)
                elif not want and has:  # надо снять (ошибочная)
                    if DRY:
                        print(f"  [dry] −👍 СНЯТЬ {rec.get('name','')} (id {tg}) {kind} — НЕ на сайте")
                        removed += 1
                        continue
                    if await _set_reaction(mid, remove=True):
                        ledger.get(tg, {}).pop(kind, None)
                        if not ledger.get(tg):
                            ledger.pop(tg, None)
                        removed += 1
                        print(f"  −👍 снял {rec.get('name','')} (id {tg}) {kind}", flush=True)
                        json.dump(ledger, open(LEDGER, "w"), ensure_ascii=False)
                else:
                    skipped += 1

        if not DRY:
            json.dump(ledger, open(LEDGER, "w"), ensure_ascii=False, indent=1)
        missing = [tg for tg in site if tg not in found]
        pfx = "[DRY] " if DRY else ""
        print(f"{pfx}поставлено: {added}, снято ошибочных: {removed}, без изменений: {skipped}")
        print(f"на сайте ({len(site)}), но нет поста в группе: {len(missing)}"
              + (f" {missing[:5]}" if missing else ""))


if __name__ == "__main__":
    asyncio.run(main())
