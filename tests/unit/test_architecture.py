"""Layering rules, enforced on every test run.

    domain  ←  services  ←  bot / api (interfaces)
       ↑                         ↑
     infra (adapters)  ──────────┘ (only the Telegram client)
    di.py / main.py / worker.py: composition root and entrypoints, may import anything.
"""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).parent.parent.parent / "app"
FRAMEWORKS = {"sqlalchemy", "fastapi", "starlette", "httpx", "arq", "redis", "dishka", "asyncpg", "alembic",
              "uvicorn", "boto3", "botocore", "PIL"}

# layer -> (allowed app.* prefixes, forbidden third-party top-level packages)
RULES: dict[str, tuple[set[str], set[str]]] = {
    "app.domain": ({"app.domain"}, FRAMEWORKS),
    "app.services": ({"app.domain", "app.services"}, FRAMEWORKS),
    "app.infra": ({"app.domain", "app.infra", "app.logging_setup"}, {"fastapi", "starlette", "dishka", "uvicorn"}),
    "app.i18n": ({"app.domain", "app.i18n"}, FRAMEWORKS),
    "app.bot": ({"app.domain", "app.services", "app.bot", "app.i18n", "app.infra.telegram"},
                FRAMEWORKS - {"dishka"}),
    "app.api": ({"app.domain", "app.services", "app.api", "app.config", "app.infra.telegram"},
                {"sqlalchemy", "arq", "redis", "asyncpg"}),
}


def module_name(path: Path, app_dir: Path = APP) -> str:
    rel = path.relative_to(app_dir.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imports_of(path: Path, app_dir: Path = APP) -> set[str]:
    tree = ast.parse(path.read_text())
    name = module_name(path, app_dir)
    package = name if path.name == "__init__.py" else name.rsplit(".", 1)[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module else []))
                if node.module is None:
                    found.update(f"{target}.{alias.name}" for alias in node.names)
                else:
                    found.add(target)
            else:
                found.add(node.module)
    return found


def layer_of(module: str) -> str | None:
    return next((layer for layer in RULES if module == layer or module.startswith(layer + ".")), None)


def violations(app_dir: Path = APP) -> list[str]:
    problems = []
    for path in app_dir.rglob("*.py"):
        module = module_name(path, app_dir)
        layer = layer_of(module)
        if layer is None:
            continue
        allowed, forbidden = RULES[layer]
        for imported in imports_of(path, app_dir):
            if imported.startswith("app"):
                if not any(imported == a or imported.startswith(a + ".") for a in allowed):
                    problems.append(f"{module} imports {imported}")
            elif imported.split(".")[0] in forbidden:
                problems.append(f"{module} imports framework {imported}")
    return problems


def test_layers_only_import_what_they_are_allowed_to():
    assert violations() == []


@pytest.mark.parametrize("layer", list(RULES))
def test_every_layer_exists(layer):
    assert (APP / Path(*layer.split(".")[1:])).exists()


def test_checker_catches_a_violation(tmp_path):
    """Guard against a silently broken checker."""
    fake_app = tmp_path / "app"
    (fake_app / "domain").mkdir(parents=True)
    (fake_app / "__init__.py").write_text("")
    (fake_app / "domain" / "__init__.py").write_text("")
    (fake_app / "domain" / "bad.py").write_text("from ..infra.db import Base\nimport sqlalchemy\n")
    found = violations(fake_app)
    assert "app.domain.bad imports app.infra.db" in found
    assert "app.domain.bad imports framework sqlalchemy" in found
