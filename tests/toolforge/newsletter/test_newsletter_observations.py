"""Verify durable facts and the saved newsletter reader."""

# unittest checks persisted corruption and injected filesystem failures.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from data.newsletter.observations import save_observation
from data.newsletter.schema import read_newsletter_data, valid_newsletter
from data.prepared import read_values, write_values
from newsletter.test_newsletter_data import Reader
from tasks.zhwiki.newsletter.collection import collect_source_facts


class NewsletterObservationTests(TestCase):
    """Keep complete observations usable without a source reader."""

    def setUp(self) -> None:
        """Prepare isolated storage and synthetic facts."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "observations"
        self.now = dt.datetime(2027, 1, 5, tzinfo=dt.UTC)
        self.facts = collect_source_facts(Reader(), now=self.now)

    def test_saved_observation_reads_without_sources(
        self,
    ) -> None:
        """Read saved facts when acquisition is unavailable."""
        assert read_newsletter_data(self.directory) is None
        save_observation(self.directory, self.facts, valid_newsletter)
        with patch(
            "tasks.zhwiki.newsletter.collection.collect_source_facts",
            side_effect=AssertionError("Source access during saved read"),
        ):
            assert read_newsletter_data(self.directory) == self.facts
        pointer = read_values(self.directory / "current.parquet")
        assert (
            self.directory / "20270105/observation-000000000000.parquet"
        ).is_file()
        assert pointer["facts"] == self.facts

    def test_invalid_new_facts_preserve_last_complete_generation(self) -> None:
        """Reject invalid facts before changing the selected data."""
        save_observation(self.directory, self.facts, valid_newsletter)
        pointer = (self.directory / "current.parquet").read_bytes()
        invalid = deepcopy(self.facts)
        invalid["issues"]["2027-01"]["overview"]["total"] = -1
        with self.assertRaises(ValueError):
            save_observation(self.directory, invalid, valid_newsletter)
        assert (self.directory / "current.parquet").read_bytes() == pointer
        assert read_newsletter_data(self.directory) == self.facts

    def test_older_observation_cannot_replace_newer_but_is_retained(
        self,
    ) -> None:
        """Retain old results without moving the latest pointer back."""
        save_observation(self.directory, self.facts, valid_newsletter)
        older = collect_source_facts(
            Reader(),
            now=self.now - dt.timedelta(days=1),
        )
        save_observation(self.directory, older, valid_newsletter)
        assert read_newsletter_data(self.directory) == self.facts
        generations = list(self.directory.rglob("*.parquet"))
        assert len(generations) == 3  # ruff: ignore[magic-value-comparison]

    def test_modified_generation_fails_digest_validation(self) -> None:
        """Reject payloads with mismatched digests."""
        save_observation(self.directory, self.facts, valid_newsletter)
        path = self.directory / "current.parquet"
        generation = read_values(path)
        generation["facts"]["issues"]["2027-01"]["overview"]["total"] = 42
        write_values(path, generation)
        with self.assertRaises(ValueError):
            read_newsletter_data(self.directory)

    def test_optional_attribution_lists_validate_old_and_new_records(
        self,
    ) -> None:
        """Accept omitted lists and reject unsafe contributor names."""
        assert valid_newsletter(self.facts)
        for field, section in (
            ("authors", "quality_changes"),
            ("contributors", "dyk"),
        ):
            for names, expected in (
                (["Editor"], True),
                ([], True),
                (["Bad|name"], False),
            ):
                with self.subTest(field=field, names=names):
                    facts = deepcopy(self.facts)
                    annual = facts["issues"]["2027-01"]["year_review"]
                    if section == "quality_changes":
                        annual[section] = [
                            {
                                "title": "Game",
                                "date": "2026-01-01",
                                "grade": "GA",
                                "action": "added",
                                field: names,
                            },
                        ]
                    else:
                        annual[section][0][field] = names
                    assert valid_newsletter(facts) is expected
