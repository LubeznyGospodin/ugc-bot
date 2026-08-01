"""
Догрузка медиа креаторов из публичных ссылок на Я.Диск (которые они присылают в анкете
вместо загрузки файлов) в БД бота: фото → creator_photos, видео → creator_works.

Зачем: без медиа карточку в канал собрать нечем, и бот не может показать заявку админу.
Тот же забор встроен в бота (bot/handlers/placement.import_from_cloud) для новых анкет —
этот скрипт разгребает НАКОПЛЕННЫЙ бэклог пачкой.

Идемпотентно: добирает только недостающее до комплекта (2 фото + 4 видео), уже собранное
и решённых (placed/rejected) пропускает. Видео нормализует (H.264 + обложка + размеры) —
см. docs/CHANNEL_POSTING.md.

Запуск:  .venv/bin/python cloud_import.py          — весь бэклог
         .venv/bin/python cloud_import.py 123 456  — точечно по tg_id
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import urllib.request

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bot.cards import short_name  # noqa: E402
from bot.utils.clouddl import download, find_yadisk, list_public, split_media  # noqa: E402
from bot.utils.video import make_thumb, probe_dims, to_playable_mp4  # noqa: E402

SP = "/private/tmp/claude-501/-Users-nastasyapopova-Desktop-ugc-bot-v2/64cb78ca-4669-4f1e-9e8d-4c28a3b6afab/scratchpad"
TOK = json.load(open(f"{SP}/vars_bot.json"))["BOT_TOKEN"]
DB = json.load(open(f"{SP}/vars_pg.json"))["DATABASE_PUBLIC_URL"].split("?")[0].replace(
    "postgres://", "postgresql://")
ADMIN = 357892821
NEED_PH, NEED_VD = 2, 4      # комплект карточки
TAKE_PH, TAKE_VD = 4, 4      # сколько максимум тянем
MAX_MB = 45                  # Bot API: файл больше — не загрузить


def api(method: str, payload: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOK}/{method}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


def upload(kind: str, blob: bytes, fname: str, extra: dict | None = None,
           thumb: bytes | None = None) -> tuple[str | None, str | None]:
    """Отправляем админу молча → забираем file_id → сообщение удаляем."""
    b = "----wbCI"
    parts: list[bytes] = []

    def fld(n: str, v) -> None:
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{n}\"\r\n\r\n{v}\r\n".encode())

    fld("chat_id", ADMIN)
    fld("disable_notification", "true")
    for k, v in (extra or {}).items():
        fld(k, v)
    ctype = "image/jpeg" if kind == "photo" else "video/mp4"
    parts.append(
        f"--{b}\r\nContent-Disposition: form-data; name=\"{kind}\"; filename=\"{fname}\"\r\n"
        f"Content-Type: {ctype}\r\n\r\n".encode())
    parts += [blob, b"\r\n"]
    if thumb:
        parts.append(
            f"--{b}\r\nContent-Disposition: form-data; name=\"thumbnail\"; filename=\"t.jpg\"\r\n"
            f"Content-Type: image/jpeg\r\n\r\n".encode())
        parts += [thumb, b"\r\n"]
    parts.append(f"--{b}--\r\n".encode())
    method = "sendPhoto" if kind == "photo" else "sendVideo"
    for _ in range(2):
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TOK}/{method}", data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        try:
            r = json.load(urllib.request.urlopen(req, timeout=300))
        except urllib.error.HTTPError as e:
            j = json.loads(e.read().decode())
            if j.get("error_code") == 429:
                time.sleep(j["parameters"]["retry_after"] + 2)
                continue
            return None, j.get("description")
        except Exception as e:  # noqa: BLE001
            return None, str(e)[:60]
        if not r.get("ok"):
            return None, r.get("description")
        res = r["result"]
        api("deleteMessage", {"chat_id": ADMIN, "message_id": res["message_id"]})
        fid = res["photo"][-1]["file_id"] if kind == "photo" else (res.get("video") or {}).get("file_id")
        return fid, None
    return None, "429"


async def process(c: asyncpg.Connection, tg: int) -> tuple[int, int]:
    r = await c.fetchrow(
        "select full_name,photo,portfolio,instagram,other_socials,placement from creators where tg_id=$1", tg)
    if not r or r["placement"] in ("placed", "rejected"):
        return 0, 0
    link = (find_yadisk(r["photo"]) or find_yadisk(r["portfolio"])
            or find_yadisk(r["instagram"]) or find_yadisk(r["other_socials"]))
    if not link:
        return 0, 0
    have_ph = await c.fetchval("select count(*) from creator_photos where tg_id=$1 and kind='photo'", tg)
    have_vd = await c.fetchval("select count(*) from creator_works where tg_id=$1 and project='base'", tg)
    if have_ph >= NEED_PH and have_vd >= NEED_VD:
        return 0, 0
    try:
        items = list_public(link)
    except Exception as e:  # noqa: BLE001
        print(f"  {short_name(r['full_name'])[:18]}: ссылка недоступна ({str(e)[:30]})", flush=True)
        return 0, 0
    cloud_ph, cloud_vd = split_media(items)
    nph = nvd = 0

    for it in cloud_ph[: max(0, TAKE_PH - have_ph)]:
        if (it.get("size") or 0) > MAX_MB * 1e6:
            continue
        try:
            fid, _ = upload("photo", download(link, it["path"]), it["name"])
            if fid:
                await c.execute(
                    "insert into creator_photos(tg_id,kind,file_id,created_at) values($1,'photo',$2,now())",
                    tg, fid)
                nph += 1
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)

    pos = have_vd
    for it in cloud_vd[: max(0, TAKE_VD - have_vd)]:
        if (it.get("size") or 0) > MAX_MB * 1e6:
            continue
        try:
            blob = download(link, it["path"])
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                tf.write(blob)
                tmp = tf.name
            out = tmp + ".h264.mp4"
            try:
                src = out if to_playable_mp4(tmp, out) else tmp
                blob = open(src, "rb").read()
                dur, w, h = probe_dims(src)
                thumb = make_thumb(src)
            finally:
                for p in (tmp, out):
                    if os.path.exists(p):
                        os.unlink(p)
            extra = {"supports_streaming": "true"}
            if w and h:
                extra.update(width=w, height=h, duration=dur)
            fid, _ = upload("video", blob, it["name"], extra, thumb)
            if fid:
                pos += 1
                await c.execute(
                    "insert into creator_works(tg_id,project,file_id,source,position,normalized,created_at)"
                    " values($1,'base',$2,'cloud',$3,true,now())", tg, fid, pos)
                nvd += 1
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.6)
    return nph, nvd


async def main() -> None:
    args = [int(a) for a in sys.argv[1:]]
    c = await asyncpg.connect(DB)
    if args:
        targets = args
    else:
        rows = await c.fetch("""
            select tg_id from creators cr
            where (placement is null or placement='incomplete')
              and ((select count(*) from creator_photos p where p.tg_id=cr.tg_id and p.kind='photo') < $1
                or (select count(*) from creator_works w where w.tg_id=cr.tg_id and w.project='base') < $2)
            order by created_at desc""", NEED_PH, NEED_VD)
        targets = [r["tg_id"] for r in rows]
    print(f"кандидатов: {len(targets)}", flush=True)
    total_ph = total_vd = done = 0
    for i, tg in enumerate(targets, 1):
        nph, nvd = await process(c, tg)
        if nph or nvd:
            done += 1
            total_ph += nph
            total_vd += nvd
            nm = await c.fetchval("select full_name from creators where tg_id=$1", tg)
            print(f"[{i}/{len(targets)}] ✅ {short_name(nm)[:18]:18} +{nph}ф +{nvd}в", flush=True)
    await c.close()
    print(f"\nдобрано: {done} креаторов, +{total_ph} фото, +{total_vd} видео", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
