"""Retain newsletter facts for rendering when queried.

Call ``save`` before replacing the latest dashboard snapshot. Each issue
keeps its first complete observation for a reporting day, with January's
annual facts on a separate year-long calendar. Readers validate saved
facts and capture times without fetching sources or writing files.
"""

# Malformed saved JSON consistently raises ValueError.
# ruff: file-ignore[docstring-extraneous-exception, type-check-without-type-error]

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from data.newsletter.schema import _valid_year_review, valid_newsletter
from data.prepared import read_values, write_values
from shared.newsletter import Issue
from shared.support.files import encode_json

if TYPE_CHECKING:
    from collections.abc import Iterator

DEFAULT_PATH = Path("data/daily/newsletter-drafts")
_MAX_BYTES = 8 * 1024 * 1024
_MAX_INDEX_BYTES = 64 * 1024
_MAX_DATES = 92
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MAX_FEATURE_DATES = 366


def _date_directory(directory: Path, value: str) -> Path:
    """Locate a canonical compact UTC date folder.

    Returns:
        The compact destination for the reporting date.
    """
    return directory / dt.date.fromisoformat(value).strftime("%Y%m%d")


def _date(issue: Issue, value: str) -> dt.date:
    """Require a canonical reporting date inside the issue's quarter.

    Returns:
        The validated inclusive reporting date.

    Raises:
        ValueError: The value is malformed or belongs to another issue.
    """
    day = dt.date.fromisoformat(value)
    if day.isoformat() != value or not issue.start <= day < issue.end:
        message = "The daily draft date is outside its reporting quarter."
        raise ValueError(message)
    return day


def _feature_date(issue: Issue, value: str) -> dt.date:
    """Validate an annual feature date independently of the quarter.

    Returns:
        A canonical day within the January issue's reviewed year.

    Raises:
        ValueError: The issue or day falls outside the annual span.
    """
    day = dt.date.fromisoformat(value)
    if (
        issue.month != 1
        or day.isoformat() != value
        or not dt.date(issue.year - 1, 1, 1) <= day < issue.end
    ):
        message = "The feature date must belong to its reviewed year."
        raise ValueError(message)
    return day


def _stamp(value: object, day: dt.date) -> bool:
    """Check that an actual UTC observation follows the reporting day.

    Returns:
        Whether the value is an aware UTC time after the day ended.
    """
    if not isinstance(value, str):
        return False
    try:
        stamp = dt.datetime.fromisoformat(value)
        return (
            stamp.tzinfo is not None
            and stamp.utcoffset() == dt.timedelta()
            and stamp.date() > day
        )
    except ValueError:
        return False


@contextmanager
def _locked(directory: Path) -> Iterator[None]:
    """Serialize capture and catalog selection in one safe directory.

    Yields:
        Control while the directory's exclusive lock is held.

    Raises:
        OSError: The lock path is unsafe or unavailable.
    """
    descriptor = os.open(
        directory / ".lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "a+b") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            message = "Newsletter locks must be regular files."
            raise OSError(message)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def _feature_record(issue: Issue, review: dict, stamp: str) -> dict:
    """Validate annual facts and bind their original capture metadata.

    Returns:
        Structured annual observations with a source-facts digest.

    Raises:
        ValueError: Annual facts or capture dates are invalid.
    """
    through = dt.date.fromisoformat(review["observation_end"])
    day = _feature_date(issue, (through - dt.timedelta(days=1)).isoformat())
    captured = dt.datetime.fromisoformat(stamp)
    if not _stamp(stamp, day) or not _valid_year_review(
        review,
        issue,
        through,
        captured,
    ):
        message = "The annual feature capture is invalid."
        raise ValueError(message)
    return {
        "schema": 2,
        "issue": issue.value,
        "date": day.isoformat(),
        "generated_at": stamp,
        "year_review": review,
        "sha256": hashlib.sha256(encode_json(review)).hexdigest(),
    }


def _write_record(directory: Path, filename: str, record: dict) -> None:
    """Write structured facts before their calendar is advanced.

    Raises:
        OSError: A date folder is unsafe or storage fails.
    """
    destination = _date_directory(directory, record["date"])
    if destination.is_symlink():
        message = "Newsletter date folders cannot be symlinks."
        raise OSError(message)
    destination.mkdir(mode=0o700, exist_ok=True)
    write_values(destination / filename, record, max_bytes=_MAX_BYTES)


def _calendar_entry(record: dict) -> dict:
    """Return the metadata used to select an immutable observation."""
    return {key: record[key] for key in ("generated_at", "sha256")}


def _select(directory: Path, issue: Issue, dates: dict, record: dict) -> None:
    """Atomically select a complete observation in its date catalog."""
    dates[record["date"]] = _calendar_entry(record)
    write_values(
        directory / "catalog.parquet",
        {"schema": 1, "issue": issue.value, "dates": dates},
        max_bytes=_MAX_BYTES,
    )


def _catalog(directory: Path, issue: Issue, *, annual: bool = False) -> dict:
    """Read a bounded calendar without creating storage.

    Returns:
        Validated capture metadata keyed by reporting date.

    Raises:
        ValueError: Calendar identities, dates, or hashes are invalid.
    """
    try:
        value = read_values(directory / "catalog.parquet", _MAX_INDEX_BYTES)
    except FileNotFoundError:
        return {}
    dates = value.get("dates")
    maximum = _MAX_FEATURE_DATES if annual else _MAX_DATES
    if (
        type(value.get("schema")) is not int
        or value["schema"] != 1
        or value.get("issue") != issue.value
        or not isinstance(dates, dict)
        or len(dates) > maximum
    ):
        message = "The newsletter calendar is invalid."
        raise ValueError(message)
    for key, entry in dates.items():
        day = _feature_date(issue, key) if annual else _date(issue, key)
        if (
            not isinstance(entry, dict)
            or not _stamp(entry.get("generated_at"), day)
            or not isinstance(entry.get("sha256"), str)
            or _DIGEST.fullmatch(entry["sha256"]) is None
        ):
            message = "Newsletter calendar evidence is invalid."
            raise ValueError(message)
    return dates


class NewsletterDraftArchive:
    """Save and read immutable daily newsletter facts as JSON."""

    def __init__(self, path: Path | None = DEFAULT_PATH) -> None:
        """Configure durable storage without creating files.

        Args:
            path: Archive root, or none to disable storage and reads.
        """
        self.path = path

    def _directory(self, issue: Issue, *, create: bool = False) -> Path:
        """Resolve a safe issue folder, optionally creating it.

        Returns:
            The issue directory beneath the configured root.

        Raises:
            OSError: A directory is a symlink or a non-directory.
            ValueError: Storage is disabled.
        """
        if self.path is None:
            message = "Daily newsletter storage is disabled."
            raise ValueError(message)
        directory = self.path / issue.value
        for path in (self.path.parent, self.path, directory):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                message = "Daily newsletter folders must be real directories."
                raise OSError(message)
        if create:
            self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.mkdir(mode=0o700, exist_ok=True)
        return directory

    def _feature_directory(
        self,
        issue: Issue,
        *,
        create: bool = False,
    ) -> Path:
        """Resolve the annual stream without expanding quarters.

        Returns:
            A checked annual-feature directory.

        Raises:
            OSError: A stream directory is unsafe.
            ValueError: The issue is not January.
        """
        if issue.month != 1:
            message = "Only January issues have annual feature archives."
            raise ValueError(message)
        path = self._directory(issue, create=create) / "annual-feature"
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            message = "Annual feature folders must be real directories."
            raise OSError(message)
        if create:
            path.mkdir(mode=0o700, exist_ok=True)
        return path

    def _catalog(self, issue: Issue) -> dict:
        """Return the validated quarterly date catalog."""
        return _catalog(self._directory(issue), issue)

    def _feature_catalog(self, issue: Issue) -> dict:
        """Return the validated year-long feature catalog."""
        return _catalog(self._feature_directory(issue), issue, annual=True)

    @staticmethod
    def _record(issue: Issue, facts: dict, generated_at: str) -> dict:
        """Validate facts and derive their completed reporting day.

        Returns:
            An envelope with its capture time and source-facts digest.

        Raises:
            ValueError: The facts lack available completed data.
        """
        boundary = dt.date.fromisoformat(facts["observation_end"])
        if not facts["available"] or boundary <= issue.start:
            message = "Daily newsletter facts need a completed reporting day."
            raise ValueError(message)
        day = boundary - dt.timedelta(days=1)
        if not _stamp(generated_at, day) or dt.datetime.fromisoformat(
            generated_at,
        ) < dt.datetime.fromisoformat(facts["updated_at"]):
            message = "The daily capture time precedes its observation."
            raise ValueError(message)
        return {
            "schema": 2,
            "issue": issue.value,
            "date": day.isoformat(),
            "generated_at": generated_at,
            "sha256": hashlib.sha256(encode_json(facts)).hexdigest(),
            "facts": facts,
        }

    def _read_record(self, issue: Issue, day: dt.date) -> dict:
        """Validate canonical daily facts without using outputs.

        Returns:
            A validated envelope containing facts and capture metadata.

        Raises:
            ValueError: Facts, hashes, or the requested identity differ.
            OSError: A stored path is unsafe or unreadable.
        """
        directory = self._directory(issue)
        destination = _date_directory(directory, day.isoformat())
        if destination.is_symlink():
            message = "Newsletter date folders cannot be symlinks."
            raise OSError(message)
        path = destination / "observation.parquet"
        value = read_values(path, _MAX_BYTES)
        facts, generated = value.get("facts"), value.get("generated_at")
        if not isinstance(facts, dict) or not isinstance(generated, str):
            message = "The daily observation has invalid facts or time."
            raise ValueError(message)
        if not valid_newsletter({"issues": {issue.value: facts}}):
            message = "Daily newsletter facts failed snapshot validation."
            raise ValueError(message)
        expected = self._record(issue, facts, generated)
        if value != expected:
            message = "Newsletter facts failed verification."
            raise ValueError(message)
        if expected["date"] != day.isoformat():
            message = "Newsletter facts belong to a different date."
            raise ValueError(message)
        return expected

    def available_dates(self, issue: Issue) -> list[str]:
        """List actual quarterly reporting dates, oldest first.

        Args:
            issue: Publication whose date catalog is being read.

        Returns:
            Canonical dates, empty before the first capture.
        """
        return sorted(self._catalog(issue)) if self.path is not None else []

    def feature_dates(self, issue: Issue) -> list[str]:
        """List actual annual captures from January through December.

        Args:
            issue: January publication whose preceding year is reviewed.

        Returns:
            Actual saved dates, without invented historical days.
        """
        return (
            sorted(self._feature_catalog(issue))
            if self.path is not None
            else []
        )

    def identity(self, issue: Issue) -> tuple[int, int, int, int] | None:
        """Describe the saved calendar revision for page caching.

        Args:
            issue: Publication whose calendar is being observed.

        Returns:
            Device, inode, size, modification time, or none if absent.

        Raises:
            OSError: A present calendar is unsafe or unreadable.
        """
        if self.path is None:
            return None
        try:
            metadata = (self._directory(issue) / "catalog.parquet").stat(
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(metadata.st_mode):
            message = "Daily newsletter catalogs must be regular files."
            raise OSError(message)
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        )

    def read_entry(self, issue: Issue, day: dt.date) -> dict | None:
        """Read saved structured facts and capture evidence.

        Args:
            issue: Quarterly publication containing the reporting date.
            day: Inclusive completed UTC reporting date.

        Returns:
            Validated facts and metadata, or none for an uncaptured day.

        Raises:
            ValueError: Date, facts, or calendar evidence is invalid.
        """
        _date(issue, day.isoformat())
        if self.path is None:
            return None
        entry = self._catalog(issue).get(day.isoformat())
        if entry is None:
            return None
        record = self._read_record(issue, day)
        if entry != _calendar_entry(record):
            message = "Newsletter calendar and observation differ."
            raise ValueError(message)
        return record

    def read(self, issue: Issue, day: dt.date) -> dict | None:
        """Read archived issue facts without fetching source data.

        Args:
            issue: Quarterly publication containing the reporting date.
            day: Inclusive completed UTC reporting date.

        Returns:
            Original issue facts, or none for an uncaptured date.
        """
        record = self.read_entry(issue, day)
        return record["facts"] if record is not None else None

    def _read_feature(self, issue: Issue, day: dt.date) -> dict:
        """Validate annual facts independently of old output files.

        Returns:
            Review facts and capture metadata without generated text.

        Raises:
            ValueError: Stored facts or identity fail verification.
            OSError: A saved path is unsafe or unreadable.
        """
        directory = _date_directory(
            self._feature_directory(issue),
            day.isoformat(),
        )
        if directory.is_symlink():
            message = "Annual feature date folders cannot be symlinks."
            raise OSError(message)
        value = read_values(directory / "metadata.parquet", _MAX_BYTES)
        try:
            expected = _feature_record(
                issue,
                value["year_review"],
                value["generated_at"],
            )
        except KeyError, TypeError:
            message = "Annual feature metadata is invalid."
            raise ValueError(message) from None
        if value != expected:
            message = "Annual facts failed verification."
            raise ValueError(message)
        if expected["date"] != day.isoformat():
            message = "Annual facts belong to a different date."
            raise ValueError(message)
        return expected

    def read_feature(self, issue: Issue, day: dt.date) -> dict | None:
        """Read one annual review's saved structured facts.

        Args:
            issue: January publication containing the annual feature.
            day: Completed date within its preceding calendar year.

        Returns:
            Original review facts and capture time, or none.

        Raises:
            ValueError: The date or saved evidence is invalid.
        """
        _feature_date(issue, day.isoformat())
        if self.path is None:
            return None
        entry = self._feature_catalog(issue).get(day.isoformat())
        if entry is None:
            return None
        record = self._read_feature(issue, day)
        if entry != _calendar_entry(record):
            message = "Annual feature calendar and facts differ."
            raise ValueError(message)
        return record

    def save_feature(
        self,
        issue: Issue,
        review: dict,
        *,
        generated_at: dt.datetime,
    ) -> None:
        """Save the first annual fact observation for a completed day.

        Args:
            issue: January publication containing the annual feature.
            review: Annual facts with an exclusive observation end.
            generated_at: Actual aware capture time, retained in UTC.

        Raises:
            ValueError: Facts or capture evidence are invalid.
            OSError: Safe locking or atomic storage fails.
        """
        if self.path is None:
            return
        if generated_at.tzinfo is None or generated_at.utcoffset() is None:
            message = "Annual capture requires an aware timestamp."
            raise ValueError(message)
        record = _feature_record(
            issue,
            review,
            generated_at.astimezone(dt.UTC).isoformat(timespec="seconds"),
        )
        directory = self._feature_directory(issue, create=True)
        with _locked(directory):
            dates = self._feature_catalog(issue)
            day = dt.date.fromisoformat(record["date"])
            if record["date"] in dates:
                self.read_feature(issue, day)
                return
            try:
                record = self._read_feature(issue, day)
            except FileNotFoundError:
                _write_record(directory, "metadata.parquet", record)
            _select(directory, issue, dates, record)

    def _capture_feature(
        self,
        issue: Issue,
        facts: dict,
        generated: dt.datetime,
    ) -> None:
        """Capture annual facts before the independent quarter gate."""
        review = facts.get("year_review")
        if issue.month != 1 or not isinstance(review, dict):
            return
        through = dt.date.fromisoformat(review["observation_end"])
        if through > dt.date(issue.year - 1, 1, 1):
            self.save_feature(issue, review, generated_at=generated)

    def save(
        self,
        data: dict,
        *,
        generated_at: dt.datetime | None = None,
    ) -> None:
        """Retain each day's first complete structured observations.

        Args:
            data: Valid newsletter facts; unelapsed quarterly dates are
                skipped independently of the annual feature stream.
            generated_at: Actual aware capture time. Defaults to the
                current UTC clock, separate from source observations.

        Raises:
            OSError: Safe locking or atomic storage fails.
            ValueError: Facts or existing durable evidence are invalid.
        """
        if self.path is None:
            return
        if not valid_newsletter(data):
            message = "Daily newsletter facts failed snapshot validation."
            raise ValueError(message)
        generated = generated_at or dt.datetime.now(dt.UTC)
        if generated.tzinfo is None or generated.utcoffset() is None:
            message = "Daily capture requires an aware timestamp."
            raise ValueError(message)
        generated = generated.astimezone(dt.UTC)
        stamp = generated.isoformat(timespec="seconds")
        for key, facts in data["issues"].items():
            issue = Issue.parse(key)
            self._capture_feature(issue, facts, generated)
            if (
                not facts["available"]
                or dt.date.fromisoformat(facts["observation_end"])
                <= issue.start
            ):
                continue
            record = self._record(issue, facts, stamp)
            directory = self._directory(issue, create=True)
            with _locked(directory):
                dates = self._catalog(issue)
                day = _date(issue, record["date"])
                if record["date"] in dates:
                    self.read_entry(issue, day)
                    continue
                try:
                    record = self._read_record(issue, day)
                except FileNotFoundError:
                    _write_record(directory, "observation.parquet", record)
                _select(directory, issue, dates, record)
