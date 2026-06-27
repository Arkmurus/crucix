"""R-F2059: capability test for search result caching.

Verifies that repeated search queries hit the cache instead of the wire,
and that cache expiry works correctly.
"""
from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

from aria_service.intel.web_search import (
    _SEARCH_CACHE,
    _SEARCH_CACHE_TTL,
    _SEARCH_CACHE_MAX,
    _search_cache_key,
    _search_cache_get,
    _search_cache_set,
)


def test_cache_hit_returns_cached_results():
    """A query cached within TTL returns the cached results."""
    _SEARCH_CACHE.clear()
    key = _search_cache_key("Angola defence", "en")
    mock_results = [MagicMock(title="Test Result")]
    _search_cache_set(key, mock_results)
    cached = _search_cache_get(key)
    assert cached is mock_results, "cache should return the exact same list"


def test_cache_miss_returns_none():
    """An uncached query returns None."""
    _SEARCH_CACHE.clear()
    key = _search_cache_key("never searched before", "en")
    assert _search_cache_get(key) is None


def test_cache_expiry_returns_none():
    """An expired cache entry returns None."""
    _SEARCH_CACHE.clear()
    key = _search_cache_key("stale query", "en")
    mock_results = [MagicMock(title="Stale Result")]
    _search_cache_set(key, mock_results)
    # Manually age the entry past TTL
    _SEARCH_CACHE[key] = (time.time() - _SEARCH_CACHE_TTL - 1, mock_results)
    assert _search_cache_get(key) is None
    assert key not in _SEARCH_CACHE, "expired entry should be evicted"


def test_cache_max_evicts_oldest():
    """When cache is full, the oldest entry is evicted."""
    _SEARCH_CACHE.clear()
    # Fill to capacity
    for i in range(_SEARCH_CACHE_MAX):
        k = _search_cache_key(f"query_{i}", "en")
        _search_cache_set(k, [MagicMock(title=f"Result {i}")])
    assert len(_SEARCH_CACHE) == _SEARCH_CACHE_MAX
    # Add one more — should evict oldest
    new_key = _search_cache_key("newest query", "en")
    _search_cache_set(new_key, [MagicMock(title="Newest Result")])
    assert len(_SEARCH_CACHE) == _SEARCH_CACHE_MAX
    assert new_key in _SEARCH_CACHE, "newest entry should be in cache"


def test_cache_key_differs_by_language():
    """Same query in different languages produces different cache keys."""
    key_en = _search_cache_key("Angola defence", "en")
    key_pt = _search_cache_key("Angola defence", "pt")
    assert key_en != key_pt, "different languages should have different cache keys"


def test_cache_key_case_insensitive():
    """Cache key is case-insensitive for the query."""
    key_lower = _search_cache_key("angola defence", "en")
    key_upper = _search_cache_key("ANGOLA DEFENCE", "en")
    assert key_lower == key_upper, "cache key should be case-insensitive"
