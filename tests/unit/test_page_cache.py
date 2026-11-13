"""Verify rendered-page expiry, identity, and memory bounds."""

from unittest import TestCase
from unittest.mock import patch

from webservice.page_cache import PageCache


class TestPageCache(TestCase):
    """Keep observation freshness independent of request frequency."""

    @staticmethod
    def test_expiry_does_not_slide_when_a_page_is_read() -> None:
        """Expire a popular page sixty seconds after rendering."""
        cache = PageCache()
        with patch(
            "webservice.page_cache.time.monotonic",
            return_value=0,
        ):
            cache.put(("/", "en"), ("snapshot",), "first")
        with patch(
            "webservice.page_cache.time.monotonic",
            return_value=59,
        ):
            assert cache.get(("/", "en"), ("snapshot",)) == "first"
        with patch(
            "webservice.page_cache.time.monotonic",
            return_value=60,
        ):
            assert cache.get(("/", "en"), ("snapshot",)) is None

    @staticmethod
    def test_inputs_and_locales_keep_independent_html() -> None:
        """Invalidate changed observations and keep locales distinct."""
        cache = PageCache()
        cache.put(("/", "en"), ("before",), "old")
        cache.put(("/", "zh-hant"), ("before",), "舊")
        assert cache.get(("/", "en"), ("after",)) is None
        assert cache.get(("/", "zh-hant"), ("before",)) == "舊"

    @staticmethod
    def test_memory_bounds_count_utf8_bytes_and_evict_oldest() -> None:
        """Bound both cached documents and their total encoded size."""
        cache = PageCache()
        with (
            patch("webservice.page_cache._MAX_BYTES", 6),
            patch("webservice.page_cache._MAX_ENTRIES", 2),
        ):
            cache.put(("first",), (), "甲")
            cache.put(("second",), (), "乙")
            assert cache.get(("first",), ()) == "甲"
            cache.put(("third",), (), "丙")
            assert cache.get(("second",), ()) is None
            assert cache.get(("first",), ()) == "甲"
            cache.put(("first",), (), "too large")
            assert cache.get(("first",), ()) is None
            cache.put(("fourth",), (), "a")
            cache.put(("fifth",), (), "b")
            assert cache.get(("third",), ()) is None

    @staticmethod
    def test_idle_expiry_releases_all_old_documents() -> None:
        """Release stale pages without another request."""
        cache = PageCache()
        with patch("webservice.page_cache.time.monotonic", return_value=0):
            cache.put(("old",), (), "甲")
        with patch("webservice.page_cache.time.monotonic", return_value=30):
            cache.put(("newer",), (), "乙")
        with patch("webservice.page_cache.time.monotonic", return_value=60):
            cache.expire()
            assert cache.get(("old",), ()) is None
            assert cache.get(("newer",), ()) == "乙"
        with patch("webservice.page_cache.time.monotonic", return_value=90):
            cache.expire()
        # A dead cache must retain neither page strings nor byte counts.
        assert vars(cache)["_entries"] == {}
        assert vars(cache)["_bytes"] == 0
