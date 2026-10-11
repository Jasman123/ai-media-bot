"""Inline keyboard helpers. callback_data is "namespace:action[:arg...]" and must fit in 64 bytes."""

from collections.abc import Callable, Sequence

CALLBACK_LIMIT = 64


def cb(*parts: object) -> str:
    data = ":".join(str(p) for p in parts)
    if len(data.encode()) > CALLBACK_LIMIT:
        raise ValueError(f"callback_data over {CALLBACK_LIMIT} bytes: {data!r}")
    return data


def btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def url_btn(text: str, url: str) -> dict:
    return {"text": text, "url": url}


def kb(rows: Sequence[Sequence[dict]]) -> dict:
    return {"inline_keyboard": [list(row) for row in rows if row]}


def main_menu(t: Callable[[str], str]) -> dict:
    return kb([
        [btn(t("menu.image"), cb("img", "new")), btn(t("menu.video"), cb("vid", "new"))],
        [btn(t("menu.animate"), cb("anim", "new")), btn(t("menu.templates"), cb("tpl", "list"))],
        [btn(t("menu.credits"), cb("cred", "show")), btn(t("menu.creations"), cb("my", "list"))],
        [btn(t("menu.settings"), cb("set", "show")), btn(t("menu.help"), cb("help", "show"))],
    ])


def language_picker(t: Callable[[str], str]) -> dict:
    return kb([[btn(t("lang.btn_id"), cb("lang", "id")), btn(t("lang.btn_en"), cb("lang", "en"))]])


def back_to_menu(t: Callable[[str], str]) -> dict:
    return kb([[btn(t("menu.back"), cb("menu", "home"))]])
