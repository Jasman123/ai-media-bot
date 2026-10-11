"""The YAML catalogs are fixtures: every key the code asks for must exist in both languages."""

import re
from pathlib import Path

from app.i18n import I18n

APP = Path(__file__).parent.parent.parent / "app"
KEY_CALL = re.compile(r"""\bt\(\s*(?:[a-z_.]+,\s*)?["']([a-z0-9_]+\.[a-z0-9_.]+)["']""")


def used_keys() -> set[str]:
    keys: set[str] = set()
    for path in APP.rglob("*.py"):
        keys.update(KEY_CALL.findall(path.read_text()))
    return keys


def test_every_key_used_in_code_exists_in_both_catalogs():
    i18n = I18n.load()
    keys = used_keys()
    assert keys, "regex found no i18n keys; check KEY_CALL"
    for lang in ("id", "en"):
        missing = sorted(k for k in keys if k not in i18n.catalogs[lang])
        assert not missing, f"{lang} missing {missing}"


def test_indonesian_is_the_default_and_strings_differ():
    i18n = I18n.load()
    assert i18n.default == "id"
    assert i18n.t("id", "menu.title") != i18n.t("en", "menu.title")


def test_html_in_catalog_is_balanced():
    i18n = I18n.load()
    for lang, catalog in i18n.catalogs.items():
        for key, text in catalog.items():
            for tag in ("b", "i", "code"):
                assert text.count(f"<{tag}>") == text.count(f"</{tag}>"), f"{lang}:{key} <{tag}> unbalanced"
