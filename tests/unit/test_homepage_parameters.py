"""Verify homepage parameters and lossless legacy conversion."""

# Assertions inspect only these pure template protocol helpers.
# Exact public-wiki hook fixtures preserve source punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]
# ruff: file-ignore[pytest-unittest-raises-assertion, magic-value-comparison]

import datetime as dt
from unittest import TestCase

import mwparserfromhell
from wiki_fixtures import OfflineSite

from tasks.zhwiki.homepage_parameters import (
    AmbiguousArticleHistoryError,
    HomepageAppearance,
    HomepageSuggestions,
    build_parameter_suggestions,
    extract_dyk_archive_entry,
    extract_dyk_entry,
    homepage_dates,
    prepare_history_suggestions,
)

_ALIASES = ("Article history", "Template:条目历史")
_LEGACY_ALIASES = ("DYKtalk", "Template:DYK talk")
_ARCHIVE_ALIASES = ("Template:DYKEntry/archive",)
_ARCHIVE_HOOK = (
    "'''[[狀況開始！| 哪套]]'''戀愛冒險遊戲為"
    "[[日本]][[工畫堂工作室]]狐組的唯一作品？"
)
_ARCHIVE = (
    "{{DYKEntry/archive|article=狀況開始！|question="
    + _ARCHIVE_HOOK
    + "|result=+|revid=15270689}}"
)
_FIRST = dt.date(2020, 1, 2)
_SECOND = dt.date(2021, 2, 3)


class TestHomepageParameters(TestCase):
    """Reserve populated slots and require actual companion evidence."""

    def setUp(self) -> None:
        """Use real title normalization without network access."""
        self.site = OfflineSite("zh", "wikipedia")

    def suggest(
        self,
        text: str,
        *appearances: HomepageAppearance,
    ) -> dict[str, str]:
        """Prepare parameter values using the audited alias fixture.

        Returns:
            Suggested values in their canonical group and slot order.
        """
        return build_parameter_suggestions(
            text,
            appearances,
            aliases=_ALIASES,
            site=self.site,
        )

    def test_all_five_groups_use_supported_parameters(self) -> None:
        """Keep repeated FA/GA dates and independent notice slots."""
        result = self.suggest(
            "",
            HomepageAppearance("maindateFA", _FIRST),
            HomepageAppearance("maindateFA", _SECOND),
            HomepageAppearance("maindateGA", _FIRST),
            HomepageAppearance(
                "itndate",
                _FIRST,
                link="Wikipedia:新闻动态/2020年1月2日",
            ),
            HomepageAppearance("itndate", _SECOND),
            HomepageAppearance(
                "dykdate",
                _FIRST,
                entry="Which '''[[Game]]''' used {{nowrap|A|B}}?",
            ),
            HomepageAppearance("dykdate", _SECOND),
            HomepageAppearance(
                "otddate",
                _FIRST,
                article_revision=123,
                link="Wikipedia:历史上的今天/1月2日",
            ),
            HomepageAppearance("otddate", _SECOND),
        )
        assert result == {
            "maindateFA": "<!-- 2020-01-02 --> 2021-02-03",
            "maindateGA": "2020-01-02",
            "itn1date": "2020-01-02",
            "itn1link": "Wikipedia:新闻动态/2020年1月2日",
            "itn2date": "2021-02-03",
            "dyk1date": "2020-01-02",
            "dyk1entry": "Which '''[[Game]]''' used {{nowrap|A|B}}?",
            "dyk2date": "2021-02-03",
            "otd1date": "2020-01-02",
            "otd1oldid": "123",
            "otd1link": "Wikipedia:历史上的今天/1月2日",
            "otd2date": "2021-02-03",
        }

    def test_existing_and_companion_only_slots_keep_their_numbers(
        self,
    ) -> None:
        """Reserve slots containing unrelated details."""
        result = self.suggest(
            "{{Article history|dykentry=Reserved question|dyk2date=2020-01-02"
            "|dyk4entry=Another reserved question|itnlink=Reserved link}}",
            HomepageAppearance("dykdate", _FIRST, entry="Historical question"),
            HomepageAppearance("dykdate", _SECOND, entry="Second question"),
            HomepageAppearance("itndate", _FIRST),
        )
        assert result == {
            "itn1date": "2020-01-02",
            "dyk2entry": "Historical question",
            "dyk1date": "2021-02-03",
            "dyk1entry": "Second question",
        }

    def test_correct_existing_values_are_not_replaced(self) -> None:
        """Retain existing dates and populated notice companions."""
        assert self.suggest(
            "{{条目历史|maindateFA=2025-01-01|dykdate=2020年1月2日"
            "|dykentry=Existing hook|otddate=2020-01-02|otdoldid=321"
            "|otdlink=Existing/source}}",
            HomepageAppearance("maindateFA", _SECOND),
            HomepageAppearance("dykdate", _FIRST, entry="Another hook"),
            HomepageAppearance(
                "otddate",
                _FIRST,
                article_revision=123,
                link="Another/source",
            ),
        ) == {"maindateFA": "<!-- 2021-02-03 --> 2025-01-01"}

    def test_repeated_dates_preserve_manual_history_and_are_idempotent(
        self,
    ) -> None:
        """Fill every proven date without duplicating existing dates."""
        assert self.suggest(
            "{{Article history|maindateFA=2021年2月3日、2025-01-01}}",
            HomepageAppearance("maindateFA", _FIRST),
            HomepageAppearance("maindateFA", _SECOND),
        ) == {
            "maindateFA": "<!-- 2020-01-02, 2021-02-03 --> 2025-01-01",
        }
        assert (
            self.suggest(
                "{{Article history|maindateFA="
                "<!-- 2020-01-02 --> 2021-02-03}}",
                HomepageAppearance("maindateFA", _FIRST),
                HomepageAppearance("maindateFA", _SECOND),
            )
            == {}
        )
        assert (
            self.suggest(
                "{{Article history|maindateFA=2021-02-03, uncertain date}}",
                HomepageAppearance("maindateFA", _FIRST),
            )
            == {}
        )

    def test_three_featured_dates_and_good_date_repair_without_new_facts(
        self,
    ) -> None:
        """Retain all user dates with one active scalar per field."""
        original = (
            "{{Article history"
            "|maindateFA=2018-10-30,2022-07-23,2026-10-13"
            "|maindateGA=2018-09-16}}"
        )
        expected = "<!-- 2018-10-30, 2022-07-23 --> 2026-10-13"
        prepared = prepare_history_suggestions(
            original,
            (),
            aliases=_ALIASES,
            site=self.site,
        )
        assert prepared.parameters == {"maindateFA": expected}
        assert prepared.talk_appearances == (
            HomepageAppearance("maindateFA", dt.date(2018, 10, 30)),
            HomepageAppearance("maindateFA", dt.date(2022, 7, 23)),
            HomepageAppearance("maindateFA", dt.date(2026, 10, 13)),
            HomepageAppearance("maindateGA", dt.date(2018, 9, 16)),
        )
        assert prepared.parameter_appearances["maindateFA"] == (
            HomepageAppearance("maindateFA", dt.date(2026, 10, 13))
        )
        code = mwparserfromhell.parse(original)
        history = code.filter_templates()[0]
        history.add("maindateFA", expected)
        assert str(history.get("maindateGA").value) == "2018-09-16"
        assert "maindateFA2" not in str(code)
        repeated = prepare_history_suggestions(
            str(code),
            (),
            aliases=_ALIASES,
            site=self.site,
        )
        assert repeated.parameters == {}
        assert repeated.talk_appearances == prepared.talk_appearances

    def test_date_only_comments_deduplicate_and_sort_all_retained_dates(
        self,
    ) -> None:
        """Repair comments while retaining every historical date."""
        value = (
            "<!-- 2021-02-03, 2020-01-02, --> <!-- 2021-02-03 --> 2025-01-01"
        )
        assert homepage_dates(value) == (
            _FIRST,
            _SECOND,
            dt.date(2025, 1, 1),
        )
        assert self.suggest(
            "{{Article history|maindateGA=" + value + "}}",
        ) == {
            "maindateGA": "<!-- 2020-01-02, 2021-02-03 --> 2025-01-01",
        }

    def test_editorial_comments_and_malformed_dates_withhold_updates(
        self,
    ) -> None:
        """Preserve uncertain text without inventing date evidence."""
        for field in ("maindateFA", "maindateGA"):
            for value in (
                "<!-- reviewed on 2018-10-30 --> 2026-10-13",
                "<!-- scheduled for 2099-01-01 --> 2026-10-13",
                "<!-- 2018-10-30, uncertain date --> 2026-10-13",
                "<!-- 2025-02-30 --> 2026-10-13",
                "<!-- 2018-10-30 --> 2025-02-30",
                "2018-10-30, 2025-02-30",
            ):
                with self.subTest(field=field, value=value):
                    assert (
                        self.suggest(
                            "{{Article history|" + field + "=" + value + "}}",
                            HomepageAppearance(field, _FIRST),
                        )
                        == {}
                    )
        editorial = "<!-- reviewed on 2018-10-30 --> 2026-10-13"
        assert dt.date(2018, 10, 30) not in homepage_dates(editorial)

    def test_verified_future_appearance_becomes_the_single_active_date(
        self,
    ) -> None:
        """Use verified future appearances as the latest active date."""
        result = self.suggest(
            "{{Article history|maindateFA="
            "<!-- 2018-10-30, 2022-07-23 --> 2026-10-13}}",
            HomepageAppearance("maindateFA", dt.date(2099, 1, 1)),
        )
        assert result == {
            "maindateFA": (
                "<!-- 2018-10-30, 2022-07-23, 2026-10-13 --> 2099-01-01"
            ),
        }

    def test_matching_dates_get_only_known_missing_companions(self) -> None:
        """Associate hook and oldid repairs with their actual slot."""
        prepared = prepare_history_suggestions(
            "{{Article history|dyk3date=2020-01-02|otd2date=2021-02-03}}",
            (
                HomepageAppearance("dykdate", _FIRST, entry="Verified hook"),
                HomepageAppearance("otddate", _SECOND, article_revision=789),
            ),
            aliases=_ALIASES,
            site=self.site,
        )
        assert prepared.parameters == {
            "dyk3entry": "Verified hook",
            "otd2oldid": "789",
        }
        assert prepared.parameter_appearances["dyk3entry"].date == _FIRST
        assert prepared.parameter_appearances["otd2oldid"].field == "otddate"

    def test_current_talk_facts_are_distinct_from_external_suggestions(
        self,
    ) -> None:
        """Retain actual scalar and numbered dates with companions."""
        prepared = prepare_history_suggestions(
            "{{Article history|maindateFA=2020年1月2日|maindateGA=2021-02-03"
            "|itn3date=2020-01-02|itn3link=Wikipedia:Actual news"
            "|dyk4date=2021-02-03|dyk4entry=Actual {{lang|en|hook}}"
            "|dykentry=Companion without a date"
            "|otd7date=2020-01-02|otd7oldid=42|otd7link=Actual/source"
            "|otd2date=2021-02-03|otd2oldid=unknown"
            "|otd8date=unknown|otd8oldid=84}}",
            (HomepageAppearance("dykdate", _FIRST, entry="External hook"),),
            aliases=_ALIASES,
            site=self.site,
        )
        assert prepared.talk_appearances == (
            HomepageAppearance("maindateFA", _FIRST),
            HomepageAppearance("maindateGA", _SECOND),
            HomepageAppearance(
                "itndate",
                _FIRST,
                link="Wikipedia:Actual news",
            ),
            HomepageAppearance(
                "dykdate",
                _SECOND,
                entry="Actual {{lang|en|hook}}",
            ),
            HomepageAppearance(
                "otddate",
                _FIRST,
                article_revision=42,
                link="Actual/source",
            ),
            HomepageAppearance("otddate", _SECOND),
        )
        assert prepared.parameters == {
            "dyk1date": "2020-01-02",
            "dyk1entry": "External hook",
        }

    def test_missing_hook_and_invalid_oldids_remain_unknown(self) -> None:
        """Never substitute source revision IDs or placeholders."""
        for oldid in (None, 0, -1, True, 2**63):
            with self.subTest(oldid=oldid):
                assert self.suggest(
                    "",
                    HomepageAppearance("dykdate", _FIRST),
                    HomepageAppearance(
                        "otddate",
                        _FIRST,
                        article_revision=oldid,
                    ),
                ) == {"dyk1date": "2020-01-02", "otd1date": "2020-01-02"}

    def test_dates_are_deduplicated_without_losing_companion_evidence(
        self,
    ) -> None:
        """Combine repeated facts into one notice slot."""
        assert self.suggest(
            "",
            HomepageAppearance("dykdate", _FIRST),
            HomepageAppearance("dykdate", _FIRST, entry="Later known hook"),
            HomepageAppearance("dykdate", _SECOND),
        ) == {
            "dyk1date": "2020-01-02",
            "dyk1entry": "Later known hook",
            "dyk2date": "2021-02-03",
        }

    def test_multiple_active_history_calls_are_ambiguous(self) -> None:
        """Require one unambiguous destination template."""
        with self.assertRaises(AmbiguousArticleHistoryError) as raised:
            self.suggest(
                "{{Article history|maindateGA=2020-01-02}}"
                "{{条目历史|dykdate=2021-02-03|dykentry=Actual hook}}",
                HomepageAppearance("dykdate", _FIRST),
            )
        assert raised.exception.template_count == 2
        assert raised.exception.talk_appearances == (
            HomepageAppearance("maindateGA", _FIRST),
            HomepageAppearance("dykdate", _SECOND, entry="Actual hook"),
        )

    def test_ambiguous_history_retains_legacy_facts_without_suggestions(
        self,
    ) -> None:
        """Retain actual current metadata when its target is unclear."""
        with self.assertRaises(AmbiguousArticleHistoryError) as raised:
            prepare_history_suggestions(
                "{{Article history|maindateFA=2020-01-02}}"
                "{{条目历史|maindateGA=2021-02-03}}"
                "{{DYKtalk|date=2020-01-02|entry=Legacy hook}}",
                [HomepageAppearance("dykdate", _SECOND, entry="Source hook")],
                aliases=_ALIASES,
                site=self.site,
                legacy_dyk_aliases=_LEGACY_ALIASES,
            )
        assert raised.exception.talk_appearances == (
            HomepageAppearance("maindateFA", _FIRST),
            HomepageAppearance("maindateGA", _SECOND),
            HomepageAppearance("dykdate", _FIRST, entry="Legacy hook"),
        )

    def test_foreign_and_explicit_article_transclusions_are_not_aliases(
        self,
    ) -> None:
        """Interpret only verified local template calls."""
        assert self.suggest(
            "{{en:Article history|dykdate=2020-01-02}}"
            "{{:Article history|dykdate=2020-01-02}}"
            "{{Wikipedia:Article history|dykdate=2020-01-02}}",
            HomepageAppearance("dykdate", _FIRST),
        ) == {"dyk1date": "2020-01-02"}

    def test_unnumbered_repair_moves_complete_group_without_collision(
        self,
    ) -> None:
        """Keep the independent unnumbered and numbered-one notices."""
        prepared = prepare_history_suggestions(
            "{{Article history|dykdate=2020-01-02"
            "|dyk1date=2021-02-03|dyk1entry=Existing second hook}}",
            [
                HomepageAppearance(
                    "dykdate",
                    _FIRST,
                    entry="Verified first hook",
                ),
            ],
            aliases=_ALIASES,
            site=self.site,
        )
        assert prepared.parameters == {
            "dyk2date": "2020-01-02",
            "dyk2entry": "Verified first hook",
        }
        assert prepared.obsolete_parameters == ("dykdate",)
        assert len(prepared.talk_appearances) == 2

    def test_optional_companion_migration_preserves_known_article_oldid(
        self,
    ) -> None:
        """Copy the old group before marking its keys removable."""
        prepared = prepare_history_suggestions(
            "{{Article history|otddate=2020-01-02|otdoldid=42}}",
            [HomepageAppearance("otddate", _FIRST, link="Actual/source")],
            aliases=_ALIASES,
            site=self.site,
        )
        assert prepared.parameters == {
            "otd1date": "2020-01-02",
            "otd1oldid": "42",
            "otd1link": "Actual/source",
        }
        assert prepared.obsolete_parameters == ("otddate", "otdoldid")

    def test_duplicate_same_day_notice_slots_do_not_guess_hook_ownership(
        self,
    ) -> None:
        """Withhold companion repairs between ambiguous rounds."""
        prepared = prepare_history_suggestions(
            "{{Article history|dykdate=2020-01-02|dyk1date=2020-01-02}}",
            [HomepageAppearance("dykdate", _FIRST, entry="Unknown round")],
            aliases=_ALIASES,
            site=self.site,
        )
        assert not prepared.parameters
        assert not prepared.obsolete_parameters


class TestHistoricalDykHook(TestCase):
    """Keep complete historical hooks and ignore incidental links."""

    def setUp(self) -> None:
        """Keep all title parsing offline."""
        self.site = OfflineSite("zh", "wikipedia")

    def test_verified_archive_matches_its_explicit_article(self) -> None:
        """Retain the archived question without rendering its markup."""
        assert (
            extract_dyk_archive_entry(
                _ARCHIVE,
                title="狀況開始！",
                aliases=_ARCHIVE_ALIASES,
                site=self.site,
            )
            == _ARCHIVE_HOOK
        )
        assert (
            extract_dyk_archive_entry(
                _ARCHIVE,
                title="Unrelated",
                aliases=_ARCHIVE_ALIASES,
                site=self.site,
            )
            is None
        )
        assert (
            extract_dyk_archive_entry(
                "<blockquote>" + _ARCHIVE + "</blockquote>",
                title="狀況開始！",
                aliases=_ARCHIVE_ALIASES,
                site=self.site,
            )
            is None
        )
        assert (
            extract_dyk_archive_entry(
                _ARCHIVE.replace("|result=+", "|result=-"),
                title="狀況開始！",
                aliases=_ARCHIVE_ALIASES,
                site=self.site,
            )
            is None
        )

    def test_primary_subject_keeps_the_complete_nested_hook(self) -> None:
        """Return markup from the matching historical question."""
        hook = "Which '''[[Game|game]]''' used {{nowrap|[[Other]]|x=y}}?"
        result = extract_dyk_entry(
            "{{Dyk/auto|0="
            + hook
            + "|1=Which '''[[Another]]''' mentions [[Game]]?}}",
            title="game",
            site=self.site,
        )
        assert result == hook
        template = mwparserfromhell.parse(
            "{{Article history|dykentry=" + result + "}}",
        ).filter_templates()[0]
        assert str(template.get("dykentry").value) == hook

    def test_quoted_and_incidental_subjects_do_not_create_hooks(self) -> None:
        """Exclude inactive samples and incidental links."""
        source = (
            "<!-- {{Dyk/auto|0='''[[Game]]''' hidden?}} -->"
            "<nowiki>{{Dyk/auto|0='''[[Game]]''' quoted?}}</nowiki>"
            "{{Dyk/auto|0=Which '''[[Other]]''' mentions [[Game]]?}}"
        )
        assert extract_dyk_entry(source, title="Game", site=self.site) is None

    def test_two_different_questions_for_one_subject_remain_unknown(
        self,
    ) -> None:
        """Keep conflicting historical questions unknown."""
        assert (
            extract_dyk_entry(
                "{{Dyk/auto|0='''[[Game]]''' first?"
                "|1='''[[Game]]''' second?}}",
                title="Game",
                site=self.site,
            )
            is None
        )

    def test_hook_preserves_refs_without_using_them_as_primary_subjects(
        self,
    ) -> None:
        """Retain hook markup and ignore cited primary links."""
        hook = "Which '''[[Game]]''' has a fact?<ref>{{cite|title=X}}</ref>"
        source = "{{Dyk/auto|0=" + hook + "}}"
        assert extract_dyk_entry(source, title="Game", site=self.site) == hook
        assert (
            extract_dyk_entry(
                "{{Dyk/auto|0=Which [[Other]]?<ref>'''[[Game]]'''</ref>}}",
                title="Game",
                site=self.site,
            )
            is None
        )


class TestStandaloneDykConversion(TestCase):
    """Count active templates and preserve their legacy facts."""

    def setUp(self) -> None:
        """Use verified aliases against offline wiki metadata."""
        self.site = OfflineSite("zh", "wikipedia")

    def test_inline_archive_supplies_hook_for_one_dated_legacy_notice(
        self,
    ) -> None:
        """Associate the archived subject with one known notice day."""
        prepared = prepare_history_suggestions(
            "{{DYKtalk|2010年|12月26日}}" + _ARCHIVE,
            [],
            aliases=_ALIASES,
            site=self.site,
            legacy_dyk_aliases=_LEGACY_ALIASES,
            article_title="狀況開始！",
            dyk_archive_aliases=_ARCHIVE_ALIASES,
        )
        assert prepared.parameters == {
            "dyk1date": "2010-12-26",
            "dyk1entry": _ARCHIVE_HOOK,
        }
        assert prepared.talk_appearances[0].entry == _ARCHIVE_HOOK
        assert prepared.legacy_appearances[0].entry is None
        assert prepared.removable_dyk_template_count == 1
        parsed = mwparserfromhell.parse(
            "{{Article history|"
            + "|".join(
                key + "=" + value for key, value in prepared.parameters.items()
            )
            + "}}",
        ).filter_templates()[0]
        assert len(parsed.params) == 2
        assert str(parsed.get("dyk1entry").value) == _ARCHIVE_HOOK

    def test_archive_without_one_known_round_does_not_supply_a_hook(
        self,
    ) -> None:
        """Keep archive questions scoped to their known DYK round."""
        prepared = prepare_history_suggestions(
            "{{DYKtalk|2010年|12月26日}}{{DYKtalk|date=2020-01-02}}"
            + _ARCHIVE,
            [],
            aliases=_ALIASES,
            site=self.site,
            legacy_dyk_aliases=_LEGACY_ALIASES,
            article_title="狀況開始！",
            dyk_archive_aliases=_ARCHIVE_ALIASES,
        )
        assert not any(key.endswith("entry") for key in prepared.parameters)

    def prepare(self, text: str) -> HomepageSuggestions:
        """Prepare a standalone conversion with one source-known hook.

        Returns:
            Parameter and safe-removal facts from active legacy notices.
        """
        return prepare_history_suggestions(
            text,
            (
                HomepageAppearance(
                    "dykdate",
                    _FIRST,
                    entry="Known historical hook",
                ),
            ),
            aliases=_ALIASES,
            site=self.site,
            legacy_dyk_aliases=_LEGACY_ALIASES,
        )

    def test_legacy_dates_and_hooks_survive_one_complete_conversion(
        self,
    ) -> None:
        """Count active aliases and exclude nested quoted samples."""
        prepared = self.prepare(
            "{{DYKtalk|2020年|1月2日}}"
            "{{Template:DYK talk|date=2021-02-03|entry=Retained hook}}"
            "<!-- {{DYKtalk|date=1999-01-01}} -->"
            "{{Some template|sample=<syntaxhighlight>"
            "{{DYKtalk|date=1998-01-01}}</syntaxhighlight>}}",
        )
        assert prepared.parameters == {
            "dyk1date": "2020-01-02",
            "dyk1entry": "Known historical hook",
            "dyk2date": "2021-02-03",
            "dyk2entry": "Retained hook",
        }
        assert prepared.article_history_present is False
        assert prepared.legacy_dyk_template_count == 2
        assert prepared.removable_dyk_template_count == 2
        assert prepared.legacy_appearances == (
            HomepageAppearance("dykdate", _FIRST),
            HomepageAppearance("dykdate", _SECOND, entry="Retained hook"),
        )
        assert prepared.talk_appearances == prepared.legacy_appearances

    def test_existing_history_gets_parameters_without_another_wrapper(
        self,
    ) -> None:
        """Merge legacy dates around existing source facts."""
        prepared = self.prepare(
            "{{Article history|dykdate=2018-01-01|dykentry=Existing}}"
            "{{DYKtalk|date=2020-01-02}}",
        )
        assert prepared.article_history_present is True
        assert prepared.parameters == {
            "dyk1date": "2020-01-02",
            "dyk1entry": "Known historical hook",
        }
        assert prepared.removable_dyk_template_count == 1
        assert prepared.talk_appearances == (
            HomepageAppearance(
                "dykdate",
                dt.date(2018, 1, 1),
                entry="Existing",
            ),
            HomepageAppearance("dykdate", _FIRST),
        )

    def test_populated_unknown_legacy_fields_block_removal(self) -> None:
        """Preserve metadata whose destination has not been verified."""
        prepared = self.prepare(
            "{{DYKtalk|date=2020-01-02|entry=Known historical hook"
            "|extra=important}}",
        )
        assert prepared.legacy_dyk_template_count == 1
        assert prepared.removable_dyk_template_count == 0
        assert prepared.legacy_appearances == (
            HomepageAppearance(
                "dykdate",
                _FIRST,
                entry="Known historical hook",
            ),
        )
        assert prepared.parameters == {
            "dyk1date": "2020-01-02",
            "dyk1entry": "Known historical hook",
        }

    def test_empty_unknown_legacy_fields_do_not_lose_information(self) -> None:
        """Allow removal when an unfamiliar field contains no data."""
        prepared = self.prepare("{{DYKtalk|date=2020-01-02|extra= }}")
        assert prepared.removable_dyk_template_count == 1

    def test_empty_duplicate_does_not_hide_unknown_legacy_metadata(
        self,
    ) -> None:
        """Keep unfamiliar populated source parameters on the page."""
        prepared = self.prepare(
            "{{DYKtalk|date=2020-01-02|extra=important|extra= }}",
        )
        assert prepared.removable_dyk_template_count == 0

    def test_uninterpreted_legacy_date_blocks_removal_hint(self) -> None:
        """Keep unsupported legacy evidence intact."""
        prepared = self.prepare(
            "{{DYKtalk|date=unknown}}{{DYKtalk|date=2020-01-02}}",
        )
        assert prepared.legacy_dyk_template_count == 2
        assert prepared.removable_dyk_template_count == 0

    def test_conflicting_legacy_hook_is_not_marked_safe_to_remove(
        self,
    ) -> None:
        """Preserve conflicting existing and legacy hooks."""
        prepared = self.prepare(
            "{{Article history|dykdate=2020-01-02|dykentry=Existing hook}}"
            "{{DYKtalk|date=2020-01-02|entry=Different retained hook}}",
        )
        assert prepared.parameters == {}
        assert prepared.removable_dyk_template_count == 0
