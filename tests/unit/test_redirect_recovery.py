"""Verify redirect keysets, preloads and resumable evidence."""

# Exercise private extraction boundaries and exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion, private-member-access, magic-value-comparison, no-self-use]

from __future__ import annotations

import math
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import URLError

import polars as pl
from wiki_fixtures import OfflineSite

from data import redirects as records
from shared.sources.redirect_scan import ScanStoppedError
from tasks.zhwiki import redirects as scan

_NOW = "2026-11-04T01:00:00+00:00"
_ALIASES = {
    "banner": ["Template:WikiProject Video games", "Template:VG"],
    "deletion": ["Template:Old vfd multi"],
}


def _revision(identifier: int, text: str | None) -> dict:
    """Build accessible or suppressed revision content.

    Returns:
        One normalized source revision.
    """
    return {
        "revid": identifier,
        "timestamp": _NOW,
        "slots": {"main": {"content": text}},
    }


def _page(title: str, identifier: int, text: str | None = "") -> Mock:
    """Represent cached Pywikibot identity and content without requests.

    Returns:
        Preloaded page whose title can differ from discovery metadata.
    """
    page = Mock()
    page.pageid = identifier
    page.latest_revision_id = 90
    page.title.side_effect = lambda *, with_ns=True: (
        title if with_ns else title.split(":", 1)[-1]
    )
    page.namespace.return_value = 1 if title.startswith("Talk:") else 0
    page.full_url.return_value = "https://zh.wikipedia.org/wiki/" + title
    page.exists.return_value = True
    page.has_content.return_value = text is not None
    page.get.return_value = text
    page.isRedirectPage.return_value = bool(text and text.startswith("#"))
    return page


def _history(title: str, identifier: int, *revisions: dict) -> dict:
    """Prepare normalized history returned by the source adapter.

    Returns:
        Page identity and bounded revision samples.
    """
    return {
        "pageid": identifier,
        "title": title,
        "fullurl": "https://zh.wikipedia.org/wiki/" + title,
        "revisions": list(revisions),
    }


def _finding(identifier: int = 2) -> dict:
    """Provide fully typed retained evidence for store tests.

    Returns:
        One positive redirect recovery observation.
    """
    return {
        "page_id": identifier - 1,
        "title": "Example",
        "url": "https://zh.wikipedia.org/wiki/Example",
        "talk_page_id": identifier,
        "talk_title": "Talk:Example",
        "talk_url": "https://zh.wikipedia.org/wiki/Talk:Example",
        "current_revision": 90,
        "current_url": "https://zh.wikipedia.org/w/index.php?oldid=90",
        "content_revision": 70,
        "content_url": "https://zh.wikipedia.org/w/index.php?oldid=70",
        "content_timestamp": _NOW,
        "banner_revision": 80,
        "banner_url": "https://zh.wikipedia.org/w/index.php?oldid=80",
        "banner_timestamp": _NOW,
        "preview": "Readable lead.",
        "observed_at": _NOW,
    }


def _discovery(identifiers: list[int]) -> pl.DataFrame:
    """Build the candidate selector columns at the database boundary.

    Returns:
        Stable talk IDs and canonical talk titles.
    """
    return pl.DataFrame(
        [
            (identifier, f"Talk:Game {identifier}")
            for identifier in identifiers
        ],
        schema={"talk_page_id": pl.Int64, "talk_title": pl.String},
        orient="row",
    )


class _PacingEvent:
    """Advance a monotonic clock without real rate-limit sleeps."""

    def __init__(self) -> None:
        """Start an unset signal at monotonic time zero."""
        self.now = 0.0
        self.waits = []
        self.stopped = False

    def wait(self, delay: float) -> bool:
        """Advance time and report cancellation.

        Returns:
            Whether shutdown was requested.
        """
        self.waits.append(delay)
        self.now += delay
        return self.stopped

    def is_set(self) -> bool:
        """Return whether the fake signal is set."""
        return self.stopped

    def set(self) -> None:
        """Request cooperative cancellation."""
        self.stopped = True


def _client(stop: _PacingEvent, identifiers: list[int]) -> Mock:
    """Create preloaded live pages for an isolated source batch.

    Returns:
        Adapter with cached current pages and an empty history.
    """
    client = Mock()
    client.stop = stop
    client.clock.side_effect = lambda: stop.now
    client.before_request = None
    client.preload.return_value = {
        identifier: _page(
            f"Talk:Game {identifier}",
            identifier,
            "{{Old vfd multi}}",
        )
        for identifier in identifiers
    }
    client.preload_titles.return_value = {
        f"Game {identifier}": _page(f"Game {identifier}", identifier + 1000)
        for identifier in identifiers
    }
    return client


class RedirectStoreTests(TestCase):
    """Check atomic evidence/checkpoint retention and corrupt inputs."""

    @override
    def setUp(self) -> None:
        """Isolate the complete durable store under a temporary root."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.path = self.root / "reports" / "redirects.parquet"

    def test_roundtrip_binds_queue_evidence_and_unknowns(self) -> None:
        """Retain findings and resumable coverage together."""
        snapshot = records.initial()
        snapshot.update(
            items=[_finding()],
            checked=[2, 3],
            checked_count=2,
            discovered_count=3,
            pending=[{"pageid": 4, "title": "Talk:Next"}],
            unavailable=[{"pageid": 3, "title": "Talk:Hidden"}],
            unavailable_count=1,
            aliases=_ALIASES,
            continuation={"continue": "-||", "eicontinue": "1|20"},
            updated_at=_NOW,
        )
        records.save(self.path, snapshot)
        assert records.read(self.path) == snapshot

    def test_invalid_selectors_do_not_replace_prior_data(self) -> None:
        """Reject damaged selectors before replacing saved facts."""
        snapshot = records.initial()
        records.save(self.path, snapshot)
        before = self.path.read_bytes()
        snapshot["continuation"] = {"titles": "untrusted override"}
        with self.assertRaises(ValueError):
            records.save(self.path, snapshot)
        assert self.path.read_bytes() == before

    def test_replica_keyset_roundtrip_preserves_scan_evidence(self) -> None:
        """Retain stable talk-page cursors alongside unfinished work."""
        snapshot = records.initial()
        snapshot.update(
            items=[_finding()],
            checked=[2],
            checked_count=1,
            discovered_count=2,
            pending=[{"pageid": 4, "title": "Talk:Next"}],
            aliases=_ALIASES,
            continuation={"pageid": "12345"},
            updated_at=_NOW,
        )
        records.save(self.path, snapshot)
        assert records.read(self.path) == snapshot

    def test_invalid_replica_keysets_preserve_prior_checkpoint(self) -> None:
        """Reject mixed, nonpositive and nondecimal cursor selectors."""
        snapshot = records.initial()
        records.save(self.path, snapshot)
        before = self.path.read_bytes()
        for cursor in (
            {"pageid": "0"},
            {"pageid": "-1"},
            {"pageid": "+1"},
            {"pageid": "1.0"},
            {"pageid": "\uff11\uff12"},
            {"pageid": ""},
            {"pageid": 123},
            {"pageid": "1", "continue": "-||"},
            {"pageid": "1", "eicontinue": "1|20"},
            {"pageids": "1"},
        ):
            with self.subTest(cursor=cursor):
                snapshot["continuation"] = cursor
                with self.assertRaises(ValueError):
                    records.save(self.path, snapshot)
                assert self.path.read_bytes() == before

    def test_failed_atomic_replace_preserves_checkpoint(self) -> None:
        """Preserve the previous generation when commit fails."""
        snapshot = records.initial()
        records.save(self.path, snapshot)
        before = self.path.read_bytes()
        with (
            patch.object(Path, "replace", side_effect=OSError("disk failure")),
            self.assertRaises(OSError),
        ):
            records.save(self.path, snapshot)
        assert self.path.read_bytes() == before

    def test_missing_and_corrupt_are_distinct(self) -> None:
        """Never treat damaged durable observations as a fresh scan."""
        with self.assertRaises(FileNotFoundError):
            records.read(self.path)
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"not parquet")
        with self.assertRaises(ValueError):
            records.read(self.path)


class RedirectExtractionTests(TestCase):
    """Check current preloads, histories and template identities."""

    def test_ignored_nested_source_does_not_match(self) -> None:
        """Distinguish literal banners from active nested calls."""
        text = (
            "<div><pre><nowiki>{{VG}}</nowiki></pre></div>"
            "<!--{{VG}}-->{{Shell|{{Old vfd multi}}}}"
        )
        assert scan._templates(text) == {"shell", "old vfd multi"}

    def test_maintenance_templates_do_not_make_redirects_article_content(
        self,
    ) -> None:
        """Skip tagged redirects and retain substantive revisions."""
        client = _client(_PacingEvent(), [2])
        client.history.return_value = _history(
            "Game",
            1,
            _revision(90, "{{Cleanup}}\n#REDIRECT [[Target]]"),
            _revision(89, "<!-- header -->{{Notability}}\n#重定向 [[Target]]"),
            _revision(70, "{{Cleanup}}'''Game''' is a video game."),
        )
        assert scan._historical_article(client, "Game", 1)["revid"] == 70
        client.history.return_value = _history(
            "Game",
            1,
            _revision(90, "{{Cleanup}}\n#REDIRECT [[Target]]"),
        )
        assert scan._historical_article(client, "Game", 1) is None

    def test_moved_talk_identity_and_historical_article_lead(self) -> None:
        """Pair a moved talk ID with its current subject title."""
        client = _client(_PacingEvent(), [2])
        client.preload.return_value = {
            2: _page("Talk:Renamed", 2, "{{Old vfd multi}}"),
        }
        client.preload_titles.return_value = {
            "Renamed": _page("Renamed", 1, "#REDIRECT [[Target]]"),
        }
        client.history.side_effect = [
            _history(
                "Talk:Renamed",
                2,
                _revision(85, "{{Old vfd multi}}"),
                _revision(80, "{{VG}}"),
            ),
            _history(
                "Renamed",
                1,
                _revision(
                    70,
                    "{{Notice}}'''Game''' is a [[video game]]."
                    "<ref>Source</ref>",
                ),
            ),
        ]
        candidate = {"pageid": 2, "title": "Talk:Old name"}
        prepared = scan._preload_candidates(client, [candidate])
        result = scan._candidate(client, candidate, _ALIASES, prepared[2])
        assert result["title"] == "Renamed"
        assert result["content_revision"] == 70
        assert result["banner_revision"] == 80
        assert result["preview"] == "Game is a video game."
        client.preload.assert_called_once_with([2])
        client.preload_titles.assert_called_once_with(("Renamed",))
        assert client.history.call_args_list[0].kwargs["page_id"] == 2
        assert client.history.call_args_list[1].kwargs["page_id"] == 1

    def test_live_nonredirects_and_present_banners_skip_history(self) -> None:
        """Recheck candidates against current talk and subject data."""
        client = _client(_PacingEvent(), [2])
        candidate = {"pageid": 2, "title": "Talk:Game 2"}
        for talk, subject in (
            ("{{Old vfd multi}}", "A restored article."),
            ("{{Old vfd multi}}{{VG}}", "#REDIRECT [[Target]]"),
            ("No current deletion notice.", "#REDIRECT [[Target]]"),
            ("#REDIRECT [[Talk:Target]]", "#REDIRECT [[Target]]"),
        ):
            with self.subTest(talk=talk, subject=subject):
                prepared = (
                    scan._current(_page("Talk:Game 2", 2, talk)),
                    scan._current(_page("Game 2", 1, subject)),
                )
                assert (
                    scan._candidate(client, candidate, _ALIASES, prepared)
                    is None
                )
        client.history.assert_not_called()

    def test_deleted_talk_id_is_a_verified_negative(self) -> None:
        """Handle successful stable-ID omission without blocking."""
        client = _client(_PacingEvent(), [])
        candidate = {"pageid": 2, "title": "Talk:Deleted"}
        prepared = scan._preload_candidates(client, [candidate])
        assert prepared == {2: (None, None)}
        assert (
            scan._candidate(client, candidate, _ALIASES, prepared[2]) is None
        )
        client.history.assert_not_called()

    def test_hidden_current_talk_is_unknown_without_refetch(self) -> None:
        """Keep suppressed preloaded text unavailable."""
        client = _client(_PacingEvent(), [2])
        talk = _page("Talk:Game 2", 2, None)
        prepared = (
            scan._current(talk),
            scan._current(_page("Game 2", 1, "#REDIRECT [[Target]]")),
        )
        with self.assertRaises(scan.RevisionUnavailableError):
            scan._candidate(client, {"pageid": 2}, _ALIASES, prepared)
        talk.get.assert_not_called()
        client.history.assert_not_called()

    def test_oldest_samples_retain_hidden_and_positive_evidence(self) -> None:
        """Find old banners while hidden article text stays unknown."""
        client = _client(_PacingEvent(), [2])
        prepared = (
            scan._current(_page("Talk:Game 2", 2, "{{Old vfd multi}}")),
            scan._current(_page("Game 2", 1, "#REDIRECT [[Target]]")),
        )
        client.history.side_effect = [
            _history(
                "Talk:Game 2",
                2,
                _revision(85, "{{Old vfd multi}}"),
                _revision(80, None),
            ),
            _history("Talk:Game 2", 2, _revision(10, "{{VG}}")),
            _history("Game 2", 1, _revision(90, None)),
            _history("Game 2", 1, _revision(70, None)),
        ]
        result = scan._candidate(client, {"pageid": 2}, _ALIASES, prepared)
        assert result["banner_revision"] == 10
        assert result["content_revision"] is None
        assert not result["preview"]
        assert client.history.call_args_list[1].kwargs["oldest"] is True

    def test_dynamic_chained_aliases_are_discovered_in_batches(self) -> None:
        """Expand incoming redirects through exact alias targets."""
        client = _client(_PacingEvent(), [])
        client.site = OfflineSite("zh", "wikipedia")

        def aliases(_site: object, title: str) -> tuple[str, ...]:
            """Return each template's direct metadata identity.

            Returns:
                Canonical identity with a known direct banner alias.
            """
            return (
                (title, "Template:VG") if title == scan._BANNER else (title,)
            )

        def incoming(_site: object, titles: list[str]) -> tuple[str, ...]:
            """Represent an indirect redirect targeting a direct alias.

            Returns:
                New aliases for this exact incoming-target frontier.
            """
            return (
                ("Template:VG old alias",) if "Template:VG" in titles else ()
            )

        with (
            patch.object(scan, "template_aliases", side_effect=aliases),
            patch.object(
                scan,
                "query_template_redirect_aliases",
                side_effect=incoming,
            ) as query,
        ):
            result = scan._aliases(client)
        assert "Template:VG old alias" in result["banner"]
        assert any(
            "Template:VG old alias" in call.args[1]
            for call in query.call_args_list
        )
        assert all(len(call.args[1]) <= 50 for call in query.call_args_list)

    def test_localized_aliases_keep_the_saved_canonical_prefix(self) -> None:
        """Accept localized metadata within the saved schema."""
        client = _client(_PacingEvent(), [])
        client.site = OfflineSite("zh", "wikipedia")

        def aliases(_site: object, title: str) -> tuple[str, ...]:
            """Return local namespace spelling from Replica metadata.

            Returns:
                Localized equivalent of the configured template.
            """
            return (title.replace("Template:", "模板:"),)

        with (
            patch.object(scan, "template_aliases", side_effect=aliases),
            patch.object(
                scan,
                "query_template_redirect_aliases",
                return_value=(),
            ),
        ):
            result = scan._aliases(client)
        assert result == {
            "banner": [scan._BANNER],
            "deletion": list(scan._DELETION),
        }
        snapshot = records.initial()
        snapshot["aliases"] = result
        records._validate(snapshot)


class RedirectDiscoveryTests(TestCase):
    """Resume Replica discovery with historical checkpoints."""

    def test_legacy_cursor_restarts_keyset_and_skips_checked_ids(self) -> None:
        """Retain evidence while switching discovery transports."""
        client = _client(_PacingEvent(), [])
        snapshot = records.initial()
        snapshot.update(
            aliases=_ALIASES,
            checked=[2],
            checked_count=1,
            discovered_count=1,
            items=[_finding()],
            continuation={"continue": "-||", "eicontinue": "1|20"},
            template_index=99,
        )
        with patch.object(
            scan,
            "query_redirect_recovery_candidates",
            return_value=_discovery([2, 4]),
        ) as query:
            scan._discover(client, snapshot)
        query.assert_called_once_with(
            client.site,
            _ALIASES["deletion"],
            after_page_id=0,
            limit=50,
        )
        assert snapshot["pending"] == [{"pageid": 4, "title": "Talk:Game 4"}]
        assert snapshot["continuation"] == {"pageid": "4"}
        assert snapshot["checked"] == [2]
        assert snapshot["items"] == [_finding()]
        assert snapshot["discovered_count"] == 2
        records._validate(snapshot)

    def test_full_checked_batch_advances_cursor_before_completion(
        self,
    ) -> None:
        """Do not treat previously checked IDs as exhaustion."""
        client = _client(_PacingEvent(), [])
        snapshot = records.initial()
        snapshot.update(
            aliases=_ALIASES,
            checked=list(range(1, 51)),
            checked_count=50,
            discovered_count=50,
        )
        with patch.object(
            scan,
            "query_redirect_recovery_candidates",
            side_effect=[_discovery(list(range(1, 51))), _discovery([])],
        ) as query:
            scan._discover(client, snapshot)
            assert snapshot["status"] == "scanning"
            assert snapshot["pending"] == []
            assert snapshot["continuation"] == {"pageid": "50"}
            scan._discover(client, snapshot)
        assert query.call_args.kwargs["after_page_id"] == 50
        assert snapshot["status"] == "complete"
        assert snapshot["checked_count"] == 50

    def test_discovery_waits_for_idle_before_database_query(self) -> None:
        """Cancel discovery when scheduled work needs priority."""
        stop = _PacingEvent()
        client = _client(stop, [])
        client.before_request = stop.set
        snapshot = records.initial()
        snapshot["aliases"] = _ALIASES
        with (
            patch.object(scan, "query_redirect_recovery_candidates") as query,
            self.assertRaises(ScanStoppedError),
        ):
            scan._discover(client, snapshot)
        query.assert_not_called()
        assert snapshot["continuation"] is None


class RedirectCandidatePacingTests(TestCase):
    """Check page pacing independently from source batching."""

    def test_fifty_pages_reuse_two_preload_batches(self) -> None:
        """Read fifty current talk and subject pages in two groups."""
        stop = _PacingEvent()
        identifiers = list(range(1, 51))
        client = _client(stop, identifiers)
        snapshot = records.initial()
        snapshot.update(
            aliases=_ALIASES,
            discovered_count=50,
            pending=[
                {"pageid": identifier, "title": f"Talk:Game {identifier}"}
                for identifier in identifiers
            ],
        )
        admissions = []
        original = scan._candidate

        def candidate(*args: object) -> dict | None:
            """Record each real extraction admission.

            Returns:
                Original candidate result from preloaded current pages.
            """
            admissions.append(stop.now)
            return original(*args)

        with (
            patch.object(scan, "_save"),
            patch.object(scan, "_candidate", side_effect=candidate),
            patch.object(
                scan,
                "query_redirect_recovery_candidates",
                return_value=_discovery([]),
            ),
        ):
            scan._run(client, Path("unused.parquet"), snapshot)
        assert math.isclose(admissions[0], 1.2)
        assert math.isclose(admissions[-1], 60)
        assert all(
            math.isclose(current - previous, 1.2)
            for previous, current in pairwise(admissions)
        )
        client.preload.assert_called_once_with(identifiers)
        assert len(client.preload_titles.call_args.args[0]) == 50
        client.history.assert_not_called()
        assert snapshot["checked"] == identifiers
        assert snapshot["status"] == "complete"

    def test_slow_preload_does_not_burst_candidate_checks(self) -> None:
        """Start page intervals after actual preload completion."""
        stop = _PacingEvent()
        client = _client(stop, [2, 3])
        original = client.preload.return_value

        def preload(_identifiers: list[int]) -> dict:
            """Spend five simulated seconds loading shared page data.

            Returns:
                Prepared current talks.
            """
            stop.now += 5
            return original

        client.preload.side_effect = preload
        snapshot = records.initial()
        snapshot.update(
            aliases=_ALIASES,
            discovered_count=2,
            pending=[
                {"pageid": 2, "title": "Talk:Game 2"},
                {"pageid": 3, "title": "Talk:Game 3"},
            ],
        )
        admissions = []
        with (
            patch.object(scan, "_save"),
            patch.object(
                scan,
                "_candidate",
                side_effect=lambda *_args: admissions.append(stop.now),
            ),
            patch.object(
                scan,
                "query_redirect_recovery_candidates",
                return_value=_discovery([]),
            ),
        ):
            scan._run(client, Path("unused.parquet"), snapshot)
        assert math.isclose(admissions[0], 5)
        assert math.isclose(admissions[1], 6.2)

    def test_cancelled_page_wait_preserves_pending_candidate(self) -> None:
        """Preserve pending work when page admission is cancelled."""
        stop = _PacingEvent()
        client = _client(stop, [2])
        pending = [{"pageid": 2, "title": "Talk:Game 2"}]
        snapshot = records.initial()
        snapshot.update(
            aliases=_ALIASES,
            discovered_count=1,
            pending=list(pending),
        )
        with (
            patch.object(stop, "wait", return_value=True),
            patch.object(scan, "_save") as save,
            patch.object(scan, "_candidate") as candidate,
            self.assertRaises(ScanStoppedError),
        ):
            scan._run(client, Path("unused.parquet"), snapshot)
        save.assert_not_called()
        candidate.assert_not_called()
        assert snapshot["pending"] == pending
        assert snapshot["checked_count"] == 0


class RedirectCheckpointTests(TestCase):
    """Keep candidate-level outcomes and retry positions durable."""

    @override
    def setUp(self) -> None:
        """Resume an isolated queue with a fake source clock."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.path = self.root / "redirects.parquet"
        self.stop = _PacingEvent()
        self.client = _client(self.stop, [2, 3])
        self.enterContext(
            patch.object(scan, "ScanClient", return_value=self.client),
        )
        self.enterContext(
            patch.object(
                scan,
                "query_redirect_recovery_candidates",
                return_value=_discovery([]),
            ),
        )
        snapshot = records.initial()
        snapshot.update(
            aliases=_ALIASES,
            discovered_count=2,
            pending=[
                {"pageid": 2, "title": "Talk:Game 2"},
                {"pageid": 3, "title": "Talk:Game 3"},
            ],
            continuation={"continue": "-||", "eicontinue": "1|20"},
        )
        records.save(self.path, snapshot)

    def test_shutdown_commits_finished_candidate_and_resumes_next(
        self,
    ) -> None:
        """Commit completed evidence before acknowledging shutdown."""

        def candidate(*_args: object) -> dict:
            """Cancel after observing evidence that must be committed.

            Returns:
                Complete positive finding.
            """
            self.stop.set()
            return _finding()

        with patch.object(scan, "_candidate", side_effect=candidate):
            scan.run_scan(self.stop, self.path)
        saved = records.read(self.path)
        assert saved["status"] == "interrupted"
        assert saved["checked"] == [2]
        assert saved["items"] == [_finding()]
        assert saved["pending"] == [{"pageid": 3, "title": "Talk:Game 3"}]
        self.stop.stopped = False
        with patch.object(scan, "_candidate", return_value=None) as check:
            scan.run_scan(self.stop, self.path)
        assert check.call_count == 1
        assert records.read(self.path)["status"] == "complete"

    def test_source_failure_preserves_retry_position(self) -> None:
        """Retain the candidate after an unconfirmed source failure."""
        with (
            patch.object(scan, "_candidate", side_effect=URLError("offline")),
            self.assertRaises(URLError),
        ):
            scan.run_scan(self.stop, self.path)
        saved = records.read(self.path)
        assert saved["status"] == "error"
        assert saved["checked_count"] == 0
        assert saved["pending"][0]["pageid"] == 2

    def test_hidden_talk_is_unknown_without_blocking_later_candidates(
        self,
    ) -> None:
        """Retain unavailable coverage alongside later checks."""
        with patch.object(
            scan,
            "_candidate",
            side_effect=[scan.RevisionUnavailableError("hidden"), None],
        ):
            scan.run_scan(self.stop, self.path)
        saved = records.read(self.path)
        assert saved["status"] == "complete"
        assert saved["checked_count"] == 2
        assert saved["unavailable"] == [{"pageid": 2, "title": "Talk:Game 2"}]

    def test_complete_checkpoint_refreshes_assessed_pages_daily(self) -> None:
        """Remove restored assessments without repeating the sweep."""
        snapshot = records.read(self.path)
        snapshot.update(
            status="complete",
            items=[_finding()],
            updated_at=_NOW,
        )
        records.save(self.path, snapshot)
        with patch.object(
            scan,
            "page_projects",
            return_value=pl.DataFrame({
                "page_id": [1],
                "project": ["电子游戏"],
            }),
        ) as query:
            scan.run_scan(self.stop, self.path)
            scan.run_scan(self.stop, self.path)
        query.assert_called_once()
        self.client.preload.assert_not_called()
        saved = records.read(self.path)
        assert saved["items"] == []
        assert saved["status"] == "complete"

    def test_completed_refresh_failure_preserves_worklist_for_retry(
        self,
    ) -> None:
        """Keep findings and daily coverage after source failure."""
        snapshot = records.read(self.path)
        snapshot.update(status="complete", items=[_finding()], updated_at=_NOW)
        records.save(self.path, snapshot)
        with (
            patch.object(
                scan,
                "page_projects",
                side_effect=OSError("offline"),
            ),
            self.assertRaises(OSError),
        ):
            scan.run_scan(self.stop, self.path)
        saved = records.read(self.path)
        assert saved["items"] == [_finding()]
        assert saved["status"] == "complete"
        assert saved["refreshed_at"] == _NOW
