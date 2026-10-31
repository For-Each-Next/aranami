"""Retain daily newsletter facts and the original projected drafts.

Call ``save`` before replacing the latest dashboard snapshot. Each issue
keeps its first complete observation for a reporting day. Readers return
saved facts and actual times without fetching sources or reconstructing
unavailable earlier days.
"""

# Public readers propagate their safe filesystem helpers' exceptions.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from backend.website.newsletter import valid_newsletter
from shared.newsletter import ROOT, SUFFIXES, Issue
from shared.support.files import read_json_object

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_PATH = Path("reports/newsletter-drafts-v1")
_MAX_BYTES = 8 * 1024 * 1024
_MAX_INDEX_BYTES = 64 * 1024
_MAX_DATES = 92
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _encoded(value: dict) -> bytes:
    """Return deterministic JSON for hashing and atomic storage."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


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


def _atomic_json(path: Path, value: dict) -> None:
    """Replace one complete JSON artifact with a synced regular file.

    Raises:
        OSError: A destination is unsafe or the write fails.
        ValueError: The encoded artifact exceeds the storage limit.
    """
    payload = _encoded(value)
    if len(payload) > _MAX_BYTES:
        message = "The daily newsletter draft exceeds its size limit."
        raise ValueError(message)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _valid_rendered(issue: Issue, facts: dict, rendered: dict) -> bool:
    """Check a captured projection against its issue and source facts.

    Returns:
        Whether the projection supplies complete presentation fields.
    """
    if (
        rendered.get("issue") != issue.value
        or rendered.get("available") is not True
        or rendered.get("updated_at") != facts["updated_at"]
        or rendered.get("complete") is not facts["complete"]
    ):
        return False
    pages = rendered.get("pages")
    sources = rendered.get("sources")
    if (
        not isinstance(rendered.get("period"), str)
        or not isinstance(pages, list)
        or len(pages) != len(SUFFIXES)
        or not isinstance(sources, list)
    ):
        return False
    page_fields = all(
        isinstance(page, dict)
        and page.get("title")
        == f"{ROOT}/{issue.value}" + (f"/{suffix}" if suffix else "")
        and page.get("label") == (suffix or "主頁")
        and page.get("url") == issue.page_url(suffix)
        and isinstance(page.get("text"), str)
        for page, suffix in zip(pages, SUFFIXES, strict=True)
    )
    return page_fields and all(
        isinstance(source, dict)
        and isinstance(source.get("label"), str)
        and isinstance(source.get("url"), str)
        and source["url"].startswith("https://zh.wikipedia.org/")
        for source in sources
    )


class NewsletterDraftArchive:
    """Save and read each issue's immutable daily observations."""

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

    def _catalog(self, issue: Issue) -> dict:
        """Read a bounded catalog without creating directories.

        Returns:
            Validated observation metadata keyed by reporting date.

        Raises:
            OSError: A present catalog is unsafe or unreadable.
            ValueError: Catalog metadata is invalid.
        """
        path = self._directory(issue) / "catalog.json"
        try:
            catalog = read_json_object(path, _MAX_INDEX_BYTES)
        except FileNotFoundError:
            return {}
        dates = catalog.get("dates")
        if (
            type(catalog.get("schema")) is not int
            or catalog["schema"] != 1
            or catalog.get("issue") != issue.value
            or not isinstance(dates, dict)
            or len(dates) > _MAX_DATES
        ):
            message = "The daily newsletter catalog is invalid."
            raise ValueError(message)
        for value, entry in dates.items():
            day = _date(issue, value)
            if (
                not isinstance(entry, dict)
                or not _stamp(entry.get("generated_at"), day)
                or not isinstance(entry.get("sha256"), str)
                or _DIGEST.fullmatch(entry["sha256"]) is None
            ):
                message = "The daily newsletter catalog has invalid evidence."
                raise ValueError(message)
        return dates

    @staticmethod
    def _record(
        issue: Issue,
        facts: dict,
        generated_at: str,
        rendered: dict | None = None,
    ) -> dict:
        """Validate facts and derive their completed reporting day.

        Returns:
            An envelope with its capture time and content digest.

        Raises:
            ValueError: The facts lack available completed data.
        """
        if not valid_newsletter({"issues": {issue.value: facts}}):
            message = "Daily newsletter facts failed snapshot validation."
            raise ValueError(message)
        boundary = dt.date.fromisoformat(facts["observation_end"])
        if not facts["available"] or boundary <= issue.start:
            message = "Daily newsletter facts need a completed reporting day."
            raise ValueError(message)
        day = boundary - dt.timedelta(days=1)
        if not _stamp(generated_at, day) or dt.datetime.fromisoformat(
            generated_at,
        ) < dt.datetime.fromisoformat(facts["updated_at"]):
            message = "The daily draft capture time precedes its observation."
            raise ValueError(message)
        if rendered is not None and not _valid_rendered(
            issue,
            facts,
            rendered,
        ):
            message = "The projected daily newsletter draft is invalid."
            raise ValueError(message)
        return {
            "schema": 1,
            "issue": issue.value,
            "date": day.isoformat(),
            "generated_at": generated_at,
            "sha256": hashlib.sha256(
                _encoded({"facts": facts, "rendered": rendered}),
            ).hexdigest(),
            "facts": facts,
            "rendered": rendered,
        }

    def _read_record(self, issue: Issue, day: dt.date) -> dict:
        """Read and validate one immutable observation envelope.

        Returns:
            The complete validated envelope.

        Raises:
            OSError: The saved observation is unsafe or unreadable.
            ValueError: The facts, hash, or requested identity differ.
        """
        value = read_json_object(
            self._directory(issue) / f"{day.isoformat()}.json",
            _MAX_BYTES,
        )
        if type(value.get("schema")) is not int:
            message = "The daily newsletter schema is invalid."
            raise ValueError(message)
        facts = value.get("facts")
        if not isinstance(facts, dict) or not facts:
            message = "The daily newsletter observation has no valid facts."
            raise ValueError(message)
        rendered = value.get("rendered")
        if rendered is not None and not isinstance(rendered, dict):
            message = "The daily newsletter draft has invalid projected text."
            raise ValueError(message)
        generated = value.get("generated_at")
        if not isinstance(generated, str) or not generated:
            message = "The daily newsletter draft has no capture time."
            raise ValueError(message)
        expected = self._record(issue, facts, generated, rendered)
        if value != expected or value["date"] != day.isoformat():
            message = "The daily newsletter observation has invalid evidence."
            raise ValueError(message)
        return value

    def available_dates(self, issue: Issue) -> list[str]:
        """List saved reporting dates for an issue, oldest first.

        Args:
            issue: Quarterly publication whose catalog is being read.

        Returns:
            Canonical dates, empty when none have been retained.

        Raises:
            OSError: A present catalog is unsafe or unreadable.
            ValueError: The catalog is corrupt or out of bounds.
        """
        return sorted(self._catalog(issue)) if self.path is not None else []

    def identity(self, issue: Issue) -> tuple[int, int, int, int] | None:
        """Describe the saved calendar revision for page caching.

        Args:
            issue: Publication whose catalog is being observed.

        Returns:
            Device, inode, size, modification time, or none if absent.

        Raises:
            OSError: A present calendar is unsafe or unreadable.
        """
        if self.path is None:
            return None
        try:
            metadata = (self._directory(issue) / "catalog.json").stat(
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

    def read(self, issue: Issue, day: dt.date) -> dict | None:
        """Read archived draft facts without fetching source data.

        Args:
            issue: Quarterly publication containing the reporting date.
            day: Inclusive completed UTC reporting date.

        Returns:
            Original issue facts, or none for a date never archived.

        Raises:
            OSError: The catalog or observation is unsafe/unreadable.
            ValueError: The date, facts, or stored evidence are invalid.
        """
        record = self.read_entry(issue, day)
        return record["facts"] if record is not None else None

    def read_rendered(self, issue: Issue, day: dt.date) -> dict | None:
        """Return the original projected text when it was captured.

        Args:
            issue: Quarterly publication containing the reporting date.
            day: Inclusive completed UTC reporting date.

        Returns:
            The original rendered draft, or none if it was not retained.

        Raises:
            OSError: The catalog or observation is unsafe/unreadable.
            ValueError: The date, facts, or stored evidence are invalid.
        """
        record = self.read_entry(issue, day)
        return record["rendered"] if record is not None else None

    def read_entry(self, issue: Issue, day: dt.date) -> dict | None:
        """Read original facts and projected draft with their times.

        Args:
            issue: Quarterly publication containing the reporting date.
            day: Inclusive completed UTC reporting date.

        Returns:
            The validated envelope, or none for a date never retained.

        Raises:
            OSError: The catalog or observation is unsafe/unreadable.
            ValueError: The date, facts, or stored evidence are invalid.
        """
        _date(issue, day.isoformat())
        if self.path is None:
            return None
        entry = self._catalog(issue).get(day.isoformat())
        if entry is None:
            return None
        record = self._read_record(issue, day)
        if entry != {
            "generated_at": record["generated_at"],
            "sha256": record["sha256"],
        }:
            message = "The daily newsletter catalog and observation differ."
            raise ValueError(message)
        return record

    def save(
        self,
        data: dict,
        *,
        projector: Callable[[Issue, dict], dict] | None = None,
        generated_at: dt.datetime | None = None,
    ) -> None:
        """Retain an issue's first complete observation for each day.

        Args:
            data: Validated newsletter result containing issue facts.
                Unavailable issues and unelapsed quarters make no files.
            projector: Optional caller-owned wikitext renderer, invoked
                only when capturing a new daily observation.
            generated_at: Actual capture time, separate from the facts'
                source observation. Defaults to the current UTC clock.

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
            message = "Daily draft capture requires an aware UTC timestamp."
            raise ValueError(message)
        stamp = generated.astimezone(dt.UTC).isoformat(timespec="seconds")
        for key, facts in data["issues"].items():
            issue = Issue.parse(key)
            if (
                not facts["available"]
                or dt.date.fromisoformat(facts["observation_end"])
                <= issue.start
            ):
                continue
            record = self._record(issue, facts, stamp)
            directory = self._directory(issue, create=True)
            descriptor = os.open(
                directory / ".lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(descriptor, "a+b") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    message = "Daily newsletter locks must be regular files."
                    raise OSError(message)
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                dates = self._catalog(issue)
                day = _date(issue, record["date"])
                if day.isoformat() in dates:
                    self.read(issue, day)
                    continue
                path = directory / f"{day.isoformat()}.json"
                try:
                    # Recover the first copy after an incomplete write.
                    record = self._read_record(issue, day)
                except FileNotFoundError:
                    record = self._record(
                        issue,
                        facts,
                        stamp,
                        projector(issue, facts) if projector else None,
                    )
                    _atomic_json(path, record)
                dates[day.isoformat()] = {
                    "generated_at": record["generated_at"],
                    "sha256": record["sha256"],
                }
                _atomic_json(
                    directory / "catalog.json",
                    {"schema": 1, "issue": issue.value, "dates": dates},
                )
