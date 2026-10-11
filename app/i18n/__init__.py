"""String catalogs (F15): Bahasa Indonesia is the default, English secondary.

Keys are dotted paths into the YAML files. Missing keys fall back to the default language, then to the key
itself, so a missing translation never crashes a handler.
"""

import string
from pathlib import Path
from typing import Any

import yaml

from ..domain.users import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

_DIR = Path(__file__).parent


def _flatten(tree: dict[str, Any], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in tree.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, path + "."))
        else:
            flat[path] = str(value)
    return flat


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


class I18n:
    def __init__(self, catalogs: dict[str, dict[str, str]], default: str = DEFAULT_LANGUAGE):
        self.catalogs = catalogs
        self.default = default

    @classmethod
    def load(cls, directory: Path = _DIR) -> "I18n":
        catalogs = {}
        for lang in SUPPORTED_LANGUAGES:
            with open(directory / f"{lang}.yaml", encoding="utf-8") as fh:
                catalogs[lang] = _flatten(yaml.safe_load(fh) or {})
        return cls(catalogs)

    def normalize(self, lang: str | None) -> str:
        return lang if lang in self.catalogs else self.default

    def has(self, key: str) -> bool:
        return key in self.catalogs[self.default]

    def t(self, lang: str | None, key: str, **kwargs: Any) -> str:
        template = self.catalogs.get(self.normalize(lang), {}).get(key)
        if template is None:
            template = self.catalogs[self.default].get(key, key)
        return template.format(**kwargs) if kwargs else template
