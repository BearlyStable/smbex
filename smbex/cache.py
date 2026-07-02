"""In-session directory-listing cache.

Keyed by normalized path string. Purely in-memory and **session-scoped** — it is
never written to disk, so navigation is instant within a run but nothing leaks
across runs. LRU eviction bounds memory on huge trees.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Generic, TypeVar

V = TypeVar("V")


class ListingCache(Generic[V]):
    def __init__(self, maxsize: int = 4096):
        self._data: "OrderedDict[str, V]" = OrderedDict()
        self._max = maxsize
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> V | None:
        if key in self._data:
            self._data.move_to_end(key)
            self.hits += 1
            return self._data[key]
        self.misses += 1
        return None

    def put(self, key: str, value: V) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def invalidate(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)
