"""Session listing cache: hit/miss accounting, LRU eviction, invalidation."""

from __future__ import annotations

from smbex.cache import ListingCache


def test_hit_and_miss_counters():
    c: ListingCache[list[int]] = ListingCache()
    assert c.get("x") is None
    assert c.misses == 1 and c.hits == 0
    c.put("x", [1, 2])
    assert c.get("x") == [1, 2]
    assert c.hits == 1


def test_lru_eviction_keeps_recently_used():
    c: ListingCache[int] = ListingCache(maxsize=2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")  # touch 'a' so 'b' is now least-recently-used
    c.put("c", 3)  # should evict 'b'
    assert c.get("b") is None
    assert c.get("a") == 1
    assert c.get("c") == 3


def test_invalidate_and_clear():
    c: ListingCache[int] = ListingCache()
    c.put("a", 1)
    c.invalidate("a")
    assert c.get("a") is None
    c.put("b", 2)
    c.clear()
    assert "b" not in c and len(c) == 0
