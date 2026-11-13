"""Verify exact revision content batches without network requests."""

# Fixture counts and unittest exception assertions express API bounds.
# ruff: file-ignore[pytest-unittest-raises-assertion, magic-value-comparison]

from __future__ import annotations

import copy
import datetime as dt
import hashlib
from unittest import TestCase
from unittest.mock import Mock, patch

from pywikibot.exceptions import APIError

from shared.sources.revisions import RevisionContent, read_revision_contents
from shared.support.activity import activity_observer

_STAMP = "2026-11-03T08:00:00Z"


def _revision(identifier: int, text: str = "body") -> dict:
    """Build one complete exact-revision response record.

    Returns:
        Identified main-slot content with its real fixture digest.
    """
    return {
        "revid": identifier,
        "timestamp": _STAMP,
        "sha1": hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest(),
        "slots": {"main": {"content": text}},
    }


def _response(identifiers: list[int]) -> dict:
    """Group selected fixture revisions beneath one owning page.

    Returns:
        A format-version-two query response.
    """
    return {
        "query": {
            "pages": [
                {
                    "pageid": 42,
                    "title": "Talk:Game",
                    "revisions": [_revision(value) for value in identifiers],
                },
            ],
        },
    }


class TestRevisionContents(TestCase):
    """Keep exact contents and unavailable API outcomes explicit."""

    def setUp(self) -> None:
        """Configure a source client without starting requests."""
        self.site = Mock()
        self.site.dbName.return_value = "zhwiki"

    def test_order_deduplication_and_multiple_pages(self) -> None:
        """Join API page groups by revision ID rather than row order."""
        response = _response([1, 2])
        response["query"]["pages"].append({
            "pageid": 43,
            "title": "Other page",
            "revisions": [_revision(3, "")],
        })
        self.site.simple_request.return_value.submit.return_value = response
        contents = read_revision_contents(self.site, [3, 1, 3, 2])
        assert list(contents) == [3, 1, 2]
        assert contents[3] == RevisionContent(
            43,
            "Other page",
            3,
            dt.datetime(2026, 11, 3, 8, tzinfo=dt.UTC),
            hashlib.sha1(b"", usedforsecurity=False).hexdigest(),
            "",
        )
        assert contents[1].page_id == 42
        assert contents[2].text == "body"
        self.site.simple_request.assert_called_once_with(
            action="query",
            prop="revisions",
            revids=[3, 1, 2],
            rvprop="ids|timestamp|sha1|content",
            rvslots="main",
            formatversion=2,
        )

    def test_batches_fifty_and_reports_before_each_request(self) -> None:
        """Bound content queries and observe their real identities."""
        order = []

        def request(**parameters: object) -> Mock:
            """Prepare a deterministic response for the selected IDs.

            Returns:
                A request whose submit records the actual network point.
            """

            def submit() -> dict:
                """Record the request boundary and return its contents.

                Returns:
                    Complete fixture contents for this batch.
                """
                order.append("request")
                return _response(parameters["revids"])

            return Mock(submit=submit)

        self.site.simple_request.side_effect = request
        observer = Mock(side_effect=lambda *_: order.append("activity"))
        with activity_observer(observer):
            contents = read_revision_contents(self.site, range(1, 106))
        assert list(contents) == list(range(1, 106))
        assert [
            len(call.kwargs["revids"])
            for call in self.site.simple_request.call_args_list
        ] == [50, 50, 5]
        assert order == ["activity", "request"] * 3
        first = observer.call_args_list[0].args
        assert first[0] == "source_read"
        assert first[1]["dataset"] == "revision_text"
        assert first[1]["reference_count"] == 50
        assert first[1]["references"] == [
            {"kind": "revision", "value": str(value)} for value in range(1, 17)
        ]

    def test_empty_input_does_not_report_or_request(self) -> None:
        """Keep an empty selection entirely local."""
        observer = Mock()
        with activity_observer(observer):
            assert read_revision_contents(self.site, []) == {}
        observer.assert_not_called()
        self.site.simple_request.assert_not_called()

    def test_invalid_ids_fail_before_any_request(self) -> None:
        """Reject noninteger and nonpositive source identities."""
        for value in (0, -1, True, "1", 1.0, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                read_revision_contents(self.site, [1, value])
        self.site.simple_request.assert_not_called()

    def test_unavailable_content_never_uses_cached_text(self) -> None:
        """Distinguish hidden and missing content from empty text."""
        response = _response([1, 2, 3, 4])
        revisions = response["query"]["pages"][0]["revisions"]
        revisions[0]["texthidden"] = True
        revisions[1]["slots"]["main"]["sha1hidden"] = True
        del revisions[2]["slots"]["main"]["content"]
        del revisions[3]["slots"]["main"]
        response["query"]["badrevids"] = {
            "5": {"revid": 5, "missing": True},
        }
        self.site.simple_request.return_value.submit.return_value = response
        with patch("shared.sources.revisions.RevisionTextCache.get") as cached:
            assert read_revision_contents(self.site, [1, 2, 3, 4, 5]) == {
                1: None,
                2: None,
                3: None,
                4: None,
                5: None,
            }
        cached.assert_not_called()

    def test_all_missing_ids_need_no_pages(self) -> None:
        """Accept an explicit unavailable-only API result."""
        self.site.simple_request.return_value.submit.return_value = {
            "query": {"badrevids": {"1": {"revid": 1, "missing": True}}},
        }
        assert read_revision_contents(self.site, [1]) == {1: None}

    def test_timestamp_offsets_keep_the_source_instant(self) -> None:
        """Normalize an explicit offset while retaining the instant."""
        response = _response([1])
        response["query"]["pages"][0]["revisions"][0]["timestamp"] = (
            "2026-11-03T16:00:00+08:00"
        )
        self.site.simple_request.return_value.submit.return_value = response
        assert read_revision_contents(self.site, [1])[1].timestamp == (
            dt.datetime(2026, 11, 3, 8, tzinfo=dt.UTC)
        )

    def test_api_errors_are_explicit_failures(self) -> None:
        """Report API errors without claiming missing content."""
        self.site.simple_request.return_value.submit.side_effect = APIError(
            "badrevids",
            "invalid selection",
        )
        with self.assertRaises(ValueError):
            read_revision_contents(self.site, [1])

    def test_malformed_query_and_identity_results_fail(self) -> None:
        """Reject incomplete or inconsistent revision responses."""
        complete = _response([1])
        malformed = [
            {"error": {"code": "badrevids"}},
            {"query": []},
            {"query": {"pages": []}},
            {"query": {"pages": {}}},
            {**complete, "continue": {"rvcontinue": "more"}},
            _response([1, 1]),
            _response([2]),
            {"query": {"badrevids": {"2": {"revid": 2, "missing": True}}}},
        ]
        for change in (
            {"pageid": True},
            {"title": "line\nbreak"},
            {"revisions": []},
        ):
            response = copy.deepcopy(complete)
            response["query"]["pages"][0].update(change)
            malformed.append(response)
        for change in (
            {"revid": True},
            {"timestamp": "2026-11-03T08:00:00"},
            {"sha1": "not-a-hash"},
            {"slots": []},
            {"slots": {"main": []}},
            {"slots": {"main": {"content": 5}}},
        ):
            response = copy.deepcopy(complete)
            response["query"]["pages"][0]["revisions"][0].update(change)
            malformed.append(response)
        for response in malformed:
            with (
                self.subTest(response=response),
                self.assertRaises(ValueError),
            ):
                self.site.simple_request.return_value.submit.return_value = (
                    response
                )
                read_revision_contents(self.site, [1])
