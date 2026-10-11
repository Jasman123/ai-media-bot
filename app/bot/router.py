"""Handler registry (C5): commands, callback namespaces, wizard-state text steps and update events.

Handlers are plain async functions. The first parameter is the per-update `Ctx`; every further parameter is a
dependency resolved by type from the DI container, e.g.

    @router.callback("lang")
    async def choose_language(ctx: Ctx, users: UserService) -> None: ...
"""

import inspect
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

EVENT_KINDS = frozenset({
    "text", "photo", "voice", "video", "pre_checkout", "successful_payment", "refunded_payment",
    "inline_query", "my_chat_member", "unknown_command", "unknown_callback",
})


@dataclass(frozen=True)
class HandlerSpec:
    fn: Callable[..., Awaitable[None]]
    deps: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, fn: Callable[..., Awaitable[None]]) -> "HandlerSpec":
        params = list(inspect.signature(fn).parameters)
        if not params:
            raise TypeError(f"handler {fn.__qualname__} must take ctx as first parameter")
        hints = typing.get_type_hints(fn)
        missing = [p for p in params[1:] if p not in hints]
        if missing:
            raise TypeError(f"handler {fn.__qualname__}: dependencies need type hints: {missing}")
        return cls(fn=fn, deps={p: hints[p] for p in params[1:]})


class Router:
    def __init__(self) -> None:
        self.commands: dict[str, HandlerSpec] = {}
        self.callbacks: dict[str, HandlerSpec] = {}
        self.states: dict[str, HandlerSpec] = {}
        self.events: dict[str, HandlerSpec] = {}

    def command(self, *names: str):
        def deco(fn):
            spec = HandlerSpec.of(fn)
            for name in names:
                self.commands[name.lstrip("/").lower()] = spec
            return fn
        return deco

    def callback(self, namespace: str):
        def deco(fn):
            self.callbacks[namespace] = HandlerSpec.of(fn)
            return fn
        return deco

    def state(self, step: str):
        """Free-text input while the user's wizard is at `step`."""
        def deco(fn):
            self.states[step] = HandlerSpec.of(fn)
            return fn
        return deco

    def on(self, kind: str):
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind}")

        def deco(fn):
            self.events[kind] = HandlerSpec.of(fn)
            return fn
        return deco

    def include(self, other: "Router") -> None:
        for name in ("commands", "callbacks", "states", "events"):
            mine, theirs = getattr(self, name), getattr(other, name)
            clash = set(mine) & set(theirs)
            if clash:
                raise ValueError(f"duplicate {name}: {sorted(clash)}")
            mine.update(theirs)

    def dependency_types(self) -> set[Any]:
        """Every type some handler asks for; used to validate DI wiring at startup and in tests."""
        specs = [*self.commands.values(), *self.callbacks.values(), *self.states.values(), *self.events.values()]
        return {t for spec in specs for t in spec.deps.values()}
