#!/usr/bin/env python3
"""Опубликовать инструкцию «Ключ WB» (для клиента) в публичный Selectel.
Шрифты и лого вшиваются в файл — страница самодостаточна.

URL: https://24dcb37f-27c8-4dd8-84f9-6fe3e085d5df.selstorage.ru/papkids/wb-token.html
"""
import base64
import os

from papkids_dash_publish import HERE, upload

PUB = "https://24dcb37f-27c8-4dd8-84f9-6fe3e085d5df.selstorage.ru/papkids/wb-token.html"


def render():
    d = os.path.join(HERE, "papkids_dash")
    html = open(os.path.join(d, "wb_token.html"), encoding="utf-8").read()
    fonts = open(os.path.join(d, "fonts.css"), encoding="utf-8").read()
    logo = base64.b64encode(open(os.path.join(d, "logo.png"), "rb").read()).decode()
    html = html.replace("/*__FONTS__*/", fonts, 1).replace("__LOGO__", logo, 1)
    assert "__LOGO__" not in html and "/*__FONTS__*/" not in html, "плейсхолдеры не заменились"
    return ('<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            + html)


if __name__ == "__main__":
    upload(render(), "papkids/wb-token.html")
    print("опубликовано →", PUB)
