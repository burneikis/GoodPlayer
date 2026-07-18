import numpy as np

from src.video_decoder import LRUCache


def _frame(v):
    return np.full((2, 2, 3), v, dtype=np.uint8)


def test_put_and_get():
    cache = LRUCache(max_size=4)
    cache.put(1, _frame(1))
    assert np.array_equal(cache.get(1), _frame(1))
    assert cache.get(2) is None


def test_contains():
    cache = LRUCache(max_size=2)
    cache.put(1, _frame(1))
    assert 1 in cache
    assert 2 not in cache


def test_eviction_is_lru():
    cache = LRUCache(max_size=2)
    cache.put(1, _frame(1))
    cache.put(2, _frame(2))
    cache.get(1)  # Touch 1 so 2 becomes least recently used
    cache.put(3, _frame(3))
    assert 1 in cache
    assert 2 not in cache
    assert 3 in cache


def test_put_existing_key_does_not_evict():
    cache = LRUCache(max_size=2)
    cache.put(1, _frame(1))
    cache.put(2, _frame(2))
    cache.put(1, _frame(1))  # Re-put; nothing should be evicted
    assert 1 in cache and 2 in cache


def test_resize_evicts_oldest():
    cache = LRUCache(max_size=4)
    for i in range(4):
        cache.put(i, _frame(i))
    cache.resize(2)
    assert 0 not in cache and 1 not in cache
    assert 2 in cache and 3 in cache


def test_clear_resets_stats():
    cache = LRUCache(max_size=2)
    cache.put(1, _frame(1))
    cache.get(1)
    cache.get(9)
    cache.clear()
    stats = cache.stats
    assert stats["size"] == 0
    assert stats["hits"] == 0
    assert stats["misses"] == 0


def test_hit_rate():
    cache = LRUCache(max_size=2)
    cache.put(1, _frame(1))
    cache.get(1)
    cache.get(2)
    assert cache.stats["hit_rate"] == 0.5
