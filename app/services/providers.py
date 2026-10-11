from collections.abc import Iterable

from ..domain.errors import NotFound
from ..domain.generation import MediaProvider


class ProviderRegistry:
    """name → adapter instance, built once from settings in app/di.py (plan §6.1 registry.py).

    The core looks providers up by the name stored in the catalog row; it never branches on a vendor name.
    """

    def __init__(self, providers: Iterable[MediaProvider] = ()):
        self._providers: dict[str, MediaProvider] = {}
        for provider in providers:
            if provider.name in self._providers:
                raise ValueError(f"provider {provider.name!r} registered twice")
            self._providers[provider.name] = provider

    def get(self, name: str) -> MediaProvider:
        try:
            return self._providers[name]
        except KeyError:
            raise NotFound(f"provider {name!r} is not registered") from None

    def has(self, name: str) -> bool:
        return name in self._providers

    @property
    def names(self) -> list[str]:
        return sorted(self._providers)
