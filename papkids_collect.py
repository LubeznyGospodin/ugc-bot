#!/usr/bin/env python3
"""PapKids-коллектор охватов. Тонкая обёртка над bot.reach — весь сбор уже там.

Даёшь ссылки → получаешь TSV (url, платформа, охват, ошибка), готовый вставить
в лист «Посты». Проверка ключей: если охват None + «нет YOUTUBE_API_KEY/VK_TOKEN» —
ключ не подхватился.

    python papkids_collect.py URL [URL ...]      # по ссылкам из аргументов
    python papkids_collect.py --file urls.txt     # по ссылкам из файла (по одной в строке)
    python papkids_collect.py --selftest          # офлайн-проверка парсеров (без сети)
"""
import sys

from bot.reach import _vk_id, _youtube_id, detect_platform, fetch_reach


def selftest() -> None:
    assert detect_platform("https://youtu.be/abcdefghijk") == "youtube"
    assert detect_platform("https://vk.ru/clip-1_2") == "vk"
    assert detect_platform("https://instagram.com/reel/Cx") == "instagram"
    assert detect_platform("https://www.tiktok.com/@x/video/1") == "tiktok"
    assert _youtube_id("https://youtube.com/shorts/abcdefghijk") == "abcdefghijk"
    assert _vk_id("https://vk.ru/video-1_2") == "-1_2"
    print("selftest OK")


def main(argv: list[str]) -> None:
    if "--selftest" in argv:
        selftest()
        return
    if "--file" in argv:
        path = argv[argv.index("--file") + 1]
        urls = [ln.strip() for ln in open(path, encoding="utf-8") if ln.strip()]
    else:
        urls = [a for a in argv if a.startswith("http")]
    if not urls:
        print(__doc__)
        return
    for url in urls:
        platform, views, err = fetch_reach(url)
        print("\t".join([url, platform, "" if views is None else str(views), err or ""]))


if __name__ == "__main__":
    main(sys.argv[1:])
