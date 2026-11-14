"""Verify canonical article history and retained homepage evidence."""

# Storage failures and concurrent writers are explicit contracts.
# Exact public-wiki hook fixtures preserve source punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]
# ruff: file-ignore[pytest-unittest-raises-assertion, magic-value-comparison, private-member-access]

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from wiki_fixtures import OfflineSite

from data.articles import history_store, homepage_data
from data.consistency import Evidence, valid_result
from data.website import read_report_cache, save_report_cache
from tasks.zhwiki import consistency

_NOW = dt.datetime(2026, 11, 3, 14, tzinfo=dt.UTC)


def _appearance(
    day: dt.date = dt.date(2019, 2, 4),
    *,
    revision: int = 123,
    entry: str | None = None,
) -> pl.DataFrame:
    """Build source evidence with explicit unknown details.

    Returns:
        Typed homepage evidence independent of talk-page revisions.
    """
    return pl.DataFrame(
        [
            {
                "wiki": "zhwiki",
                "page_id": 10,
                "title": "Game",
                "field": "dykdate" if entry else "maindateGA",
                "source_kind": "homepage",
                "date": day,
                "source_title": "Template:Dyk"
                if entry
                else "Wikipedia:優良條目展示報告",
                "source_url": f"https://zh.wikipedia.org/w/index.php?oldid={revision}",
                "source_revision": revision,
                "source_timestamp": None,
                "observed_at": _NOW,
                "entry": entry,
                "article_revision": None,
                "link": None,
            },
        ],
        schema=homepage_data.SCHEMA,
    )


def _talk(revision: int = 456) -> pl.DataFrame:
    """Build an independent real talk-page snapshot.

    Returns:
        An unchanged legacy public history schema.
    """
    return pl.DataFrame(
        [
            {
                "page_id": 10,
                "title": "Game",
                "talk_page_id": 11,
                "revision_id": revision,
                "observed_at": _NOW,
                "contract": "a" * 64,
                "events": [],
            },
        ],
        schema=history_store.SCHEMA,
    )


class TestHomepageHistory(TestCase):
    """Keep source facts and talk snapshots distinct in one file."""

    def setUp(self) -> None:
        """Use isolated canonical storage for every verification."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.path = self.root / "data/daily/articles/history.parquet"

    def test_repeated_appearances_and_proofs_survive_empty_refresh(
        self,
    ) -> None:
        """Keep dates and revisions after members depart."""
        history_store.merge_homepage(self.path, _appearance())
        history_store.merge_homepage(self.path, _appearance(revision=124))
        history_store.merge_homepage(
            self.path,
            _appearance(dt.date(2026, 11, 1)),
        )
        retained = history_store.merge_homepage(
            self.path,
            pl.DataFrame(schema=homepage_data.SCHEMA),
        )
        assert retained.height == 3
        assert retained["date"].n_unique() == 2
        assert history_store.load(self.path).is_empty()
        raw = pl.read_parquet(self.path)
        assert raw["talk_page_id"].null_count() == raw.height
        assert raw["revision_id"].null_count() == raw.height
        assert history_store.content_digest(self.path)

    def test_existing_talk_file_upgrades_without_losing_public_contract(
        self,
    ) -> None:
        """Upgrade old files while preserving talk snapshots."""
        self.path.parent.mkdir(parents=True)
        original = _talk()
        original.write_parquet(self.path)
        assert history_store.read_homepage(self.path).is_empty()
        history_store.merge_homepage(self.path, _appearance())
        assert history_store.read_versions(self.path).equals(original)
        newer = _talk(457)
        history_store.merge_save(self.path, newer)
        assert history_store.load(self.path).equals(newer)
        assert history_store.read_versions(self.path).height == 2
        assert history_store.read_homepage(self.path).height == 1

    def test_unknown_refresh_does_not_erase_known_hook(self) -> None:
        """Keep a known hook when a refresh has no hook."""
        complete = _appearance(entry="Which '''[[Game]]''' has this hook?")
        history_store.merge_homepage(self.path, complete)
        unknown = complete.with_columns(
            pl.lit(None, dtype=pl.String).alias("entry"),
            pl.lit(_NOW + dt.timedelta(hours=1)).alias("observed_at"),
        )
        merged = history_store.merge_homepage(self.path, unknown)
        assert merged.item(0, "entry") == complete.item(0, "entry")

    def test_talk_and_homepage_writers_share_one_transaction_lock(
        self,
    ) -> None:
        """Preserve both domains under concurrent observations."""
        with ThreadPoolExecutor(max_workers=2) as workers:
            talk = workers.submit(history_store.merge_save, self.path, _talk())
            display = workers.submit(
                history_store.merge_homepage,
                self.path,
                _appearance(),
            )
            talk.result()
            display.result()
        assert history_store.read_versions(self.path).height == 1
        assert history_store.read_homepage(self.path).height == 1

    def test_failed_write_and_invalid_evidence_preserve_prior_bytes(
        self,
    ) -> None:
        """Reject corruption and keep the selected canonical history."""
        history_store.merge_homepage(self.path, _appearance())
        previous = self.path.read_bytes()
        with (
            patch.object(history_store, "_write", side_effect=OSError("full")),
            self.assertRaises(OSError),
        ):
            history_store.merge_save(self.path, _talk())
        assert self.path.read_bytes() == previous
        invalid = _appearance().with_columns(pl.lit(0).alias("page_id"))
        with self.assertRaises(ValueError):
            history_store.merge_homepage(self.path, invalid)
        assert self.path.read_bytes() == previous

    def test_source_identity_survives_article_rename(self) -> None:
        """Keep one source proof across an article rename."""
        history_store.merge_homepage(self.path, _appearance())
        renamed = _appearance().with_columns(
            pl.lit("New name").alias("title"),
            pl.lit(_NOW + dt.timedelta(minutes=1)).alias("observed_at"),
        )
        merged = history_store.merge_homepage(self.path, renamed)
        assert merged.height == 1
        assert merged.item(0, "title") == "New name"


class TestHomepageConsistency(TestCase):
    """Retain source and talk facts before preparing suggestions."""

    def setUp(self) -> None:
        """Isolate canonical history and all source boundaries."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.path = self.root / "data/daily/articles/history.parquet"
        self.site = OfflineSite("zh", "wikipedia")

    def _collect(
        self,
        text: str | dict[str, str],
        *,
        appearances: list[consistency._Expectation] | None = None,
        title: str = "Game",
        revision: int = 456,
    ) -> dict:
        """Prepare a source pass with known revision identities.

        Returns:
            Validated consistency observations and manual repair facts.
        """
        reader = Mock(site=self.site, sources={})
        reader.resolve.side_effect = lambda value: value
        articles = {title: text} if isinstance(text, str) else text
        members = pl.DataFrame({
            "page_id": [10 + 2 * index for index in range(len(articles))],
            "page_namespace": [0] * len(articles),
            "full_title": list(articles),
            "pa_class": ["Start"] * len(articles),
            "pa_importance": ["Low"] * len(articles),
        })
        observed = {
            f"Talk:{article}": consistency._Observation(
                Evidence(
                    f"Talk:{article}",
                    "https://zh.wikipedia.org/w/index.php?oldid="
                    + str(revision + index),
                    revision + index,
                    _NOW.isoformat(),
                ),
                source,
                11 + 2 * index,
            )
            for index, (article, source) in enumerate(articles.items())
        }
        with (
            patch.object(
                consistency,
                "_nomination_expectations",
                return_value=[],
            ),
            patch.object(
                consistency,
                "_good_expectations",
                return_value=appearances or [],
            ),
            patch.object(consistency, "_daily_expectations", return_value=[]),
            patch.object(
                consistency,
                "_timeline_expectations",
                return_value=[],
            ),
            patch.object(
                consistency,
                "collect_list_checks",
                return_value=([], []),
            ),
            patch.object(
                consistency,
                "_observations",
                return_value=observed,
            ),
            patch.object(
                consistency,
                "template_aliases",
                side_effect=lambda _site, name: (name,),
            ),
            patch("pywikibot.Page.save") as save,
        ):
            result = consistency.collect_consistency(
                reader,
                members,
                now=_NOW,
                history_path=self.path,
            )
        save.assert_not_called()
        assert valid_result(result)
        return result

    @staticmethod
    def _expected(
        day: str,
        *,
        entry: str | None = None,
        title: str = "Game",
    ) -> consistency._Expectation:
        """Build a proven display with independently known source oldid.

        Returns:
            One source-backed display expectation.
        """
        return consistency._Expectation(
            title,
            "homepage_good",
            "homepage_history",
            "Article history",
            Evidence(
                "Wikipedia:首頁/優良條目展示報告",
                "https://zh.wikipedia.org/w/index.php?oldid=123",
                123,
                None,
            ),
            "dykdate" if entry else "maindateGA",
            day,
            entry,
        )

    def test_dates_survive_fixed_metadata_and_a_later_renamed_subject(
        self,
    ) -> None:
        """Retain displays after correction and article renames."""
        result = self._collect(
            "{{Article history}}",
            appearances=[
                self._expected("2019-02-04"),
                self._expected("2026-11-01"),
            ],
        )
        assert result["items"][0]["suggested_parameters"] == {
            "maindateGA": "<!-- 2019-02-04 --> 2026-11-01",
        }
        assert len(result["items"][0]["appearance_history"]) == 2
        fixed = self._collect(
            "{{Article history|maindateGA=<!-- 2019-02-04 --> 2026-11-01}}",
            revision=457,
        )
        assert not fixed["items"]
        retained = history_store.read_homepage(self.path)
        assert retained.filter(pl.col("source_kind") == "homepage").height == 2
        assert retained.filter(pl.col("source_kind") == "talk").height == 2
        assert history_store.read_versions(self.path).height == 2
        renamed = self._collect(
            "{{Article history}}",
            title="New name",
            revision=458,
        )
        assert {row["title"] for row in renamed["items"]} == {"New name"}

    def test_homepage_suggestions_accept_only_complete_ordered_dates(
        self,
    ) -> None:
        """Read old backups and reject unsafe commented suggestions."""
        result = self._collect(
            "{{Article history}}",
            appearances=[self._expected("2026-11-01")],
        )
        finding = result["items"][0]
        for value, expected in (
            ("2026-11-01", True),
            ("2019-02-04, 2026-11-01", True),
            ("<!-- 2019-02-04 --> 2026-11-01", True),
            ("<!-- 2018-10-30, 2022-07-23 --> 2026-10-13", True),
            ("<!-- reviewed 2019-02-04 --> 2026-11-01", False),
            ("<!-- 2019-02-04, --> 2026-11-01", False),
            ("<!-- 2026-11-01 --> 2019-02-04", False),
            ("<!-- 2019-02-04 --> 2019-02-04", False),
            ("<!-- 2019-02-30 --> 2026-11-01", False),
            ("<!-- 2019-02-04 -- --> 2026-11-01", False),
        ):
            with self.subTest(value=value):
                candidate = {
                    **result,
                    "items": [
                        {
                            **finding,
                            "suggested_parameters": {"maindateGA": value},
                        },
                    ],
                }
                assert valid_result(candidate) is expected

    def test_hook_only_defect_uses_the_actual_missing_parameter(self) -> None:
        """Suggest a known hook without claiming its date is absent."""
        result = self._collect(
            "{{Article history|dykdate=2026-11-01}}",
            appearances=[
                self._expected("2026-11-01", entry="Which '''[[Game]]'''?"),
            ],
        )
        # This fixture supplies a DYK event through the patched builder;
        # Its exact field determines the expected parameter.
        assert result["items"][0]["expected_field"] == "dyk1entry"
        assert result["items"][0]["suggested_parameters"] == {
            "dyk1date": "2026-11-01",
            "dyk1entry": "Which '''[[Game]]'''?",
        }
        assert result["items"][0]["obsolete_parameters"] == ["dykdate"]

    def test_standalone_dyk_conversion_reuses_canonical_talk_candidates(
        self,
    ) -> None:
        """Find old standalone notices using retained candidates."""
        previous = _talk().with_columns(
            pl.Series(
                "events",
                [
                    [
                        {
                            "kind": "dyk",
                            "date": dt.date(2019, 2, 4),
                            "action": "appeared",
                            "parameter": "dykdate",
                        },
                    ],
                ],
                dtype=history_store.SCHEMA["events"],
            ),
        )
        history_store.merge_save(self.path, previous)
        result = self._collect(
            "{{DYKtalk|date=2019-02-04|entry=Which '''[[Game]]'''?}}",
            revision=457,
        )
        finding = result["items"][0]
        assert not finding["article_history_present"]
        assert finding["removable_dyk_template_count"] == 1
        assert finding["suggested_parameters"] == {
            "dyk1date": "2019-02-04",
            "dyk1entry": "Which '''[[Game]]'''?",
        }
        recorded = history_store.read_homepage(self.path)
        assert set(recorded["source_kind"]) == {"talk"}
        assert recorded.item(0, "source_revision") == 457

    def test_inline_archived_hook_retains_actual_checked_talk_provenance(
        self,
    ) -> None:
        """Retain the archived question with actual talk provenance."""
        previous = _talk().with_columns(
            pl.Series(
                "events",
                [
                    [
                        {
                            "kind": "dyk",
                            "date": dt.date(2010, 12, 26),
                            "action": "appeared",
                            "parameter": "dykdate",
                        },
                    ],
                ],
                dtype=history_store.SCHEMA["events"],
            ),
        )
        history_store.merge_save(self.path, previous)
        hook = (
            "'''[[狀況開始！| 哪套]]'''戀愛冒險遊戲為"
            "[[日本]][[工畫堂工作室]]狐組的唯一作品？"
        )
        result = self._collect(
            "{{DYKtalk|2010年|12月26日}}{{DYKEntry/archive"
            "|article=狀況開始！|question="
            + hook
            + "|result=+|revid=15270689}}",
            title="狀況開始！",
            revision=81396985,
        )
        finding = result["items"][0]
        assert finding["suggested_parameters"] == {
            "dyk1date": "2010-12-26",
            "dyk1entry": hook,
        }
        recorded = history_store.read_homepage(self.path)
        assert set(recorded["source_kind"]) == {"talk"}
        assert recorded.item(0, "entry") == hook
        assert recorded.item(0, "source_revision") == 81396985
        assert recorded.item(0, "source_title") == "Talk:狀況開始！"

    def test_existing_history_gets_its_verified_archived_hook(self) -> None:
        """Repair the real existing-date Idolmaster 2 metadata case."""
        previous = _talk().with_columns(
            pl.Series(
                "events",
                [
                    [
                        {
                            "kind": "dyk",
                            "date": dt.date(2015, 5, 11),
                            "action": "appeared",
                            "parameter": "dykdate",
                        },
                    ],
                ],
                dtype=history_store.SCHEMA["events"],
            ),
        )
        history_store.merge_save(self.path, previous)
        hook = (
            "[[萬代南夢宮遊戲|萬代南夢宮]]於2011年發行的"
            "'''[[偶像大師2|哪一款遊戲續作]]'''，因為加入男性角色並讓原本的部分女性角色"
            "不能使用，而在發表會後引發玩家的反彈？"
        )
        result = self._collect(
            "{{Article history|dykdate=2015年5月11日}}{{DYKEntry/archive"
            "|article=偶像大師2|question="
            + hook
            + "|result=+|revid=35682616}}",
            title="偶像大師2",
            revision=94267651,
        )
        finding = result["items"][0]
        assert finding["expected_field"] == "dyk1entry"
        assert finding["suggested_parameters"] == {
            "dyk1date": "2015-05-11",
            "dyk1entry": hook,
        }
        assert finding["obsolete_parameters"] == ["dykdate"]
        assert finding["source_revision"] == 94267651

    def test_same_day_conflicting_hooks_keep_complete_talk_snapshots(
        self,
    ) -> None:
        """Retain both slot events without duplicate appearance keys."""
        previous = _talk().with_columns(
            pl.Series(
                "events",
                [
                    [
                        {
                            "kind": "dyk",
                            "date": dt.date(2020, 1, 2),
                            "action": "appeared",
                            "parameter": "dykdate",
                        },
                    ],
                ],
                dtype=history_store.SCHEMA["events"],
            ),
        )
        history_store.merge_save(self.path, previous)
        result = self._collect(
            "{{Article history|dykdate=2020-01-02|dykentry=First hook"
            "|dyk1date=2020-01-02|dyk1entry=Different hook}}",
            revision=457,
        )
        assert not result["items"]
        retained = history_store.read_homepage(self.path)
        assert retained.height == 1
        assert retained.item(0, "entry") is None
        snapshots = history_store.load(self.path)
        assert {
            event["question"] for event in snapshots.item(0, "events")
        } == {
            "First hook",
            "Different hook",
        }

    def test_ambiguous_article_does_not_block_other_repairs_or_cache(
        self,
    ) -> None:
        """Withhold one unsafe target and retain all checked facts."""
        with self.assertLogs(consistency.__name__, level="WARNING") as logs:
            result = self._collect(
                {
                    "Ambiguous": "{{Article history|maindateFA=2020-01-02}}"
                    "{{Article history|dykdate=2021-02-03"
                    "|dykentry=Actual hook}}",
                    "Valid": "{{Article history}}",
                },
                appearances=[
                    self._expected("2026-11-01", title="Ambiguous"),
                    self._expected("2026-11-01", title="Valid"),
                ],
            )
        assert "Ambiguous" in logs.output[0]
        assert "talk_revision=456" in logs.output[0]
        findings = {row["title"]: row for row in result["items"]}
        ambiguous = findings["Ambiguous"]
        assert ambiguous["suggested_parameters_available"] is False
        assert ambiguous["suggested_parameters"] == {}
        assert ambiguous["removable_dyk_template_count"] == 0
        assert len(ambiguous["appearance_history"]) == 3
        assert findings["Valid"]["suggested_parameters"] == {
            "maindateGA": "2026-11-01",
        }
        checks = {row["check_id"]: row for row in result["checks"]}
        assert checks["homepage_good"]["status"] == "unavailable"
        assert checks["homepage_parameters"]["status"] == "unavailable"
        assert checks["homepage_parameters"]["checked_count"] == 1
        assert checks["homepage_dyk"]["status"] == "complete"
        retained = history_store.read_homepage(self.path)
        assert retained.filter(pl.col("source_kind") == "homepage").height == 2
        assert retained.filter(pl.col("source_kind") == "talk").height == 2
        assert history_store.read_versions(self.path).height == 2
        record = {
            "schema": 2,
            "config_id": "a" * 64,
            "attempt_config_id": "a" * 64,
            "observed_at": _NOW.isoformat(timespec="seconds"),
            "attempted_at": _NOW.isoformat(timespec="seconds"),
            "failed": False,
            "data": result,
        }
        path = self.root / "consistency.parquet"
        save_report_cache(path, record)
        assert read_report_cache(path, "consistency", 100, _NOW) == record
        for updates in (
            {"suggested_parameters_available": "false"},
            {"suggested_parameters": {"maindateGA": "2026-11-01"}},
            {
                "legacy_dyk_template_count": 1,
                "removable_dyk_template_count": 1,
            },
        ):
            assert not valid_result({
                **result,
                "items": [{**ambiguous, **updates}],
            })

    def test_unexpected_suggestion_errors_still_fail_the_collection(
        self,
    ) -> None:
        """Isolate known ambiguity without hiding unrelated failures."""
        with (
            patch.object(
                consistency,
                "prepare_history_suggestions",
                side_effect=ValueError("Unexpected invalid source contract"),
            ),
            self.assertRaisesRegex(ValueError, "Unexpected invalid source"),
        ):
            self._collect(
                "{{Article history}}",
                appearances=[self._expected("2026-11-01")],
            )
