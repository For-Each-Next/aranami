"""Find redirects with a removed historical Video games talk banner.

``run_scan`` checkpoints a paced scan of Wikipedia deletion-notice
talk pages. It checks the newest ten and oldest five talk revisions.
Absence from that sample is inconclusive. Only saved evidence reaches
the website, and this task never edits a wiki.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import os
import re
from itertools import batched
from typing import TYPE_CHECKING

import mwparserfromhell
from pywikibot import Page
from pywikibot.site import Namespace

from data import redirects as records
from shared.sources.quarry.projects import (
    page_projects,
    query_redirect_recovery_candidates,
)
from shared.sources.quarry.templates import query_template_redirect_aliases
from shared.sources.redirect_scan import ScanClient, ScanStoppedError
from shared.sources.wiki import template_aliases

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from threading import Event

_BANNER = "Template:WikiProject Video games"
_DELETION = ("Template:Old vfd multi", "Template:Multidel")
RECENT_TALK_REVISIONS = 10
OLDEST_TALK_REVISIONS = 5
RECENT_ARTICLE_REVISIONS = 20
OLDEST_ARTICLE_REVISIONS = 5
_MAX_ALIASES = 256
_MAX_TEXT = 2 * 1024 * 1024
_BATCH = 50
_CANDIDATE_INTERVAL = 60 / 50
_REDIRECT = re.compile(
    r"^[\s\ufeff]*(?:<!--.*?-->\s*)*#\s*"
    r"(?:redirect|重定向|重新導向|重新定向)\s*:?\s*\[\[",
    re.IGNORECASE | re.DOTALL,
)


class RevisionUnavailableError(Exception):
    """Mark a candidate whose sampled content remains unknown."""


def _now() -> str:
    """Return the actual observation instant as an aware UTC string.

    Returns:
        ISO timestamp with an explicit UTC offset.
    """
    return dt.datetime.now(dt.UTC).isoformat()


def _name(value: str) -> str:
    """Normalize whitespace and known template namespace spellings.

    Replica-resolved canonical names and aliases supply the identity;
    matching never guesses abbreviated banner aliases.

    Returns:
        Case-folded template name without a namespace prefix.
    """
    value = re.sub(r"\s+", " ", value.replace("_", " ")).strip()
    if ":" in value and value.split(":", 1)[0].casefold() in {
        "template",
        "模板",
    }:
        value = value.split(":", 1)[1].strip()
    return value.casefold()


def _templates(text: str) -> set[str]:
    """Extract active nested calls while excluding literal source tags.

    Returns:
        Normalized names of active template invocations.
    """
    code = mwparserfromhell.parse(text)
    for tag in list(code.filter_tags(recursive=True)):
        if str(tag.tag).strip().casefold() in {
            "nowiki",
            "pre",
            "source",
            "syntaxhighlight",
            "ref",
            "references",
            "code",
        }:
            try:
                code.remove(tag, recursive=True)
            except ValueError:
                # An ignored ancestor already removed this descendant.
                continue
    return {
        _name(str(template.name))
        for template in code.filter_templates(recursive=True)
    }


def _content(revision: dict) -> str:
    """Read an observed revision, preserving suppressed text as unknown.

    Returns:
        Complete bounded content, including legitimate empty wikitext.

    Raises:
        RevisionUnavailableError: Sampled content is unavailable.
    """
    content = revision.get("slots", {}).get("main", {}).get("content")
    if not isinstance(content, str) or len(content.encode()) > _MAX_TEXT:
        message = "A sampled revision has unavailable or oversized content."
        raise RevisionUnavailableError(message)
    return content


def _history(
    client: ScanClient,
    title: str,
    count: int,
    *,
    oldest: bool = False,
    page_id: int | None = None,
) -> dict:
    """Read a bounded history slice without following redirects.

    Returns:
        Canonical page, exact revisions and public full page URL.
    """
    return client.history(title, count, oldest=oldest, page_id=page_id)


def _revision_url(revision: int) -> str:
    """Link an exact public revision independently of title renames.

    Returns:
        Canonical Chinese Wikipedia revision URL.
    """
    return f"https://zh.wikipedia.org/w/index.php?oldid={revision}"


def _admit(client: ScanClient) -> None:
    """Yield source reads and cached checks to runtime admission.

    Raises:
        ScanStoppedError: Cancellation interrupted source admission.
    """
    if client.stop.is_set():
        raise ScanStoppedError
    if client.before_request is not None:
        client.before_request()
    if client.stop.is_set():
        raise ScanStoppedError


def _alias_title(client: ScanClient, title: str) -> str:
    """Keep localized template names within the saved namespace rules.

    Returns:
        Canonical prefix with a Pywikibot-normalized local title.

    Raises:
        RuntimeError: A source alias does not name a local template.
    """
    page = Page(client.site, title)
    if page.site != client.site or page.namespace() != Namespace.TEMPLATE:
        message = "Redirect recovery aliases must be local templates."
        raise RuntimeError(message)
    return "Template:" + page.title(with_ns=False)


def _aliases(client: ScanClient) -> dict[str, list[str]]:
    """Resolve bounded canonical and chained template aliases.

    Returns:
        Explicit deletion and banner identities from Wiki Replicas.

    Raises:
        RuntimeError: Alias discovery exceeds its complete-result bound.
    """
    result = {"banner": [], "deletion": []}
    for kind, template in (
        ("banner", _BANNER),
        *(("deletion", title) for title in _DELETION),
    ):
        _admit(client)
        result[kind].extend(
            _alias_title(client, title)
            for title in template_aliases(client.site, template)
        )
        result[kind] = list(dict.fromkeys(result[kind]))
    scanned = set()
    while frontier := [
        (kind, title)
        for kind, titles in result.items()
        for title in titles
        if (kind, title) not in scanned
    ]:
        if sum(map(len, result.values())) > _MAX_ALIASES:
            message = "Redirect recovery template aliases exceed their bound."
            raise RuntimeError(message)
        for kind, titles in result.items():
            selected = [title for group, title in frontier if group == kind][
                :_BATCH
            ]
            if not selected:
                continue
            _admit(client)
            incoming = query_template_redirect_aliases(client.site, selected)
            scanned.update((kind, title) for title in selected)
            result[kind] = list(
                dict.fromkeys((
                    *titles,
                    *(_alias_title(client, title) for title in incoming),
                )),
            )
    return result


def _current(page: Page) -> dict:
    """Read canonical identity and text from an already preloaded page.

    Returns:
        Current page observation or an explicit missing-page marker.
    """
    if not page.exists():
        return {"missing": True}
    revision = page.latest_revision_id
    result = {
        "pageid": page.pageid,
        "title": page.title(),
        "ns": page.namespace(),
        "fullurl": page.full_url(),
        "lastrevid": revision,
        "revisions": [
            {
                "revid": revision,
                "slots": {
                    "main": {
                        "content": page.get(get_redirect=True)
                        if page.has_content()
                        else None,
                    },
                },
            },
        ],
    }
    if page.isRedirectPage():
        result["redirect"] = True
    return result


def _preload_candidates(
    client: ScanClient,
    candidates: list[dict],
) -> dict[int, tuple[dict | None, dict | None]]:
    """Preload live talk identities and their paired current subjects.

    Returns:
        Talk and subject observations keyed by stable discovery talk ID.
        Successfully omitted deleted talk IDs remain explicit negatives.
    """
    talks = client.preload([row["pageid"] for row in candidates])
    names = {
        page_id: page.title(with_ns=False)
        for page_id, page in talks.items()
        if page.exists() and page.namespace() == 1
    }
    subjects = client.preload_titles(tuple(dict.fromkeys(names.values())))
    return {
        row["pageid"]: (
            _current(talks[row["pageid"]]) if row["pageid"] in talks else None,
            _current(subjects[names[row["pageid"]]])
            if row["pageid"] in names
            else None,
        )
        for row in candidates
    }


def _preview(text: str) -> str:
    """Strip notices and markup from the historical article lead.

    Returns:
        At most 1,200 readable characters without external requests.
    """
    lead = mwparserfromhell.parse(text).get_sections(include_lead=True)[0]
    for node in list(lead.filter_templates(recursive=False)):
        lead.remove(node)
    for node in list(lead.filter_comments(recursive=False)):
        lead.remove(node)
    for node in list(lead.filter_tags(recursive=False)):
        if str(node.tag).strip().casefold() in {
            "ref",
            "references",
            "nowiki",
            "pre",
            "source",
            "syntaxhighlight",
            "table",
        }:
            lead.remove(node)
    for link in list(lead.filter_wikilinks(recursive=False)):
        if (
            str(link.title)
            .strip()
            .casefold()
            .startswith((
                "file:",
                "image:",
                "文件:",
                "檔案:",
                "图像:",
                "圖像:",
                "category:",
                "分类:",
                "分類:",
            ))
        ):
            lead.remove(link)
    return re.sub(
        r"\s+",
        " ",
        lead.strip_code(normalize=True, collapse=True),
    ).strip()[:1200]


def _historical_article(
    client: ScanClient,
    title: str,
    page_id: int,
) -> dict | None:
    """Find substantive content within two bounded history slices.

    Returns:
        An exact nonredirect revision or none if no sampled text exists.
    """
    for oldest, count in (
        (False, RECENT_ARTICLE_REVISIONS),
        (True, OLDEST_ARTICLE_REVISIONS),
    ):
        page = _history(client, title, count, oldest=oldest, page_id=page_id)
        for revision in page.get("revisions", []):
            try:
                text = _content(revision)
            except RevisionUnavailableError:
                continue
            # Notices can precede a redirect directive in old revisions.
            # Inspect the visible body after removing them as well.
            active = mwparserfromhell.parse(text)
            for node in [
                *active.filter_templates(recursive=False),
                *active.filter_comments(recursive=False),
            ]:
                active.remove(node)
            preview = _preview(text)
            if preview and not _REDIRECT.match(str(active)):
                return revision
    return None


def _banner_sample(
    revisions: list[dict],
    banners: set[str],
) -> tuple[dict | None, bool]:
    """Search accessible revisions while retaining unknown coverage.

    Returns:
        Matching revision and whether other sampled text was hidden.
    """
    unknown = False
    for revision in revisions:
        try:
            names = _templates(_content(revision))
        except RevisionUnavailableError:
            unknown = True
            continue
        if names & banners:
            return revision, unknown
    return None, unknown


def _qualifying_current(
    prepared: tuple[dict | None, dict | None],
    banners: set[str],
    deletion: set[str],
) -> bool:
    """Require a live nonredirect talk and redirected subject.

    Returns:
        Whether current talk text permits bounded historical research.

    Raises:
        RevisionUnavailableError: Required sampled text is unavailable.
    """  # ruff: ignore[docstring-extraneous-exception]
    identity, subject = prepared
    if identity is None or "missing" in identity:
        return False
    if identity.get("ns") != 1 or "redirect" in identity:
        return False
    if (
        subject is None
        or "missing" in subject
        or subject.get("ns") != 0
        or "redirect" not in subject
    ):
        return False
    current = _templates(_content(identity["revisions"][0]))
    return not current & banners and bool(current & deletion)


def _candidate(
    client: ScanClient,
    candidate: dict,
    aliases: dict,
    prepared: tuple[dict | None, dict | None],
) -> dict | None:
    """Check preloaded current pages and retain historical evidence.

    Returns:
        Matching evidence, or none for a verified nonmatching candidate.

    Raises:
        RevisionUnavailableError: Required sampled text is unavailable.
    """
    banners = {_name(title) for title in aliases["banner"]}
    deletion = {_name(title) for title in aliases["deletion"]}
    if not _qualifying_current(prepared, banners, deletion):
        return None
    identity, subject = prepared
    talk = _history(
        client,
        identity["title"],
        RECENT_TALK_REVISIONS,
        page_id=candidate["pageid"],
    )
    if "missing" in talk:
        return None
    recent = talk.get("revisions", [])
    if not recent:
        message = "Talk history is unavailable."
        raise RevisionUnavailableError(message)
    current = _templates(_content(recent[0]))
    if current & banners or not current & deletion:
        return None
    evidence, unknown = _banner_sample(recent[1:], banners)
    if evidence is None:
        oldest = _history(
            client,
            talk["title"],
            OLDEST_TALK_REVISIONS,
            oldest=True,
            page_id=candidate["pageid"],
        )
        evidence, older_unknown = _banner_sample(
            oldest.get("revisions", []),
            banners,
        )
        unknown = unknown or older_unknown
    if evidence is None:
        if unknown:
            message = "Some sampled talk revisions are unavailable."
            raise RevisionUnavailableError(message)
        return None
    content = _historical_article(client, subject["title"], subject["pageid"])
    current_revision = subject["lastrevid"]
    return {
        "page_id": subject["pageid"],
        "title": subject["title"],
        "url": subject["fullurl"],
        "talk_page_id": talk["pageid"],
        "talk_title": talk["title"],
        "talk_url": talk["fullurl"],
        "current_revision": current_revision,
        "current_url": _revision_url(current_revision),
        "content_revision": content["revid"] if content else None,
        "content_url": _revision_url(content["revid"]) if content else None,
        "content_timestamp": content["timestamp"] if content else None,
        "banner_revision": evidence["revid"],
        "banner_url": _revision_url(evidence["revid"]),
        "banner_timestamp": evidence["timestamp"],
        "preview": _preview(_content(content)) if content else "",
        "observed_at": _now(),
    }


def _save(path: Path, snapshot: dict) -> None:
    """Retain the actual checkpoint instant and complete evidence."""
    snapshot["updated_at"] = _now()
    records.save(path, snapshot)


def _discover(client: ScanClient, snapshot: dict) -> None:
    """Read one Replica keyset batch without losing retained progress.

    Legacy API cursors restart Replica selection at page ID zero. Saved
    checked IDs prevent repeated candidates from becoming new work.

    Raises:
        RuntimeError: Discovery coverage is malformed or unordered.
    """
    cursor = snapshot["continuation"] or {}
    after = int(cursor["pageid"]) if "pageid" in cursor else 0
    _admit(client)
    rows = query_redirect_recovery_candidates(
        client.site,
        snapshot["aliases"]["deletion"],
        after_page_id=after,
        limit=_BATCH,
    ).to_dicts()
    identifiers = [row["talk_page_id"] for row in rows]
    if (
        len(rows) > _BATCH
        or identifiers != sorted(set(identifiers))
        or any(identifier <= after for identifier in identifiers)
    ):
        message = "Redirect recovery discovery returned invalid page IDs."
        raise RuntimeError(message)
    if not rows:
        snapshot["status"] = "complete"
        return
    checked = set(snapshot["checked"])
    snapshot["pending"] = [
        {"pageid": row["talk_page_id"], "title": row["talk_title"]}
        for row in rows
        if row["talk_page_id"] not in checked
    ]
    snapshot["discovered_count"] += len(snapshot["pending"])
    snapshot["continuation"] = {"pageid": str(identifiers[-1])}
    snapshot["status"] = "scanning"


def _run(client: ScanClient, path: Path, snapshot: dict) -> None:
    """Check up to fifty candidates per minute and checkpoint each one.

    Raises:
        ScanStoppedError: Runtime shutdown stopped the scan.
    """
    if snapshot["aliases"] is None:
        snapshot["aliases"] = _aliases(client)
        _save(path, snapshot)
    prepared = {}
    next_candidate = client.clock() + _CANDIDATE_INTERVAL
    while not client.stop.is_set():
        if not snapshot["pending"]:
            _discover(client, snapshot)
            _save(path, snapshot)
            if snapshot["status"] == "complete":
                return
            continue
        if not prepared:
            prepared = _preload_candidates(client, snapshot["pending"])
        delay = max(0.0, next_candidate - client.clock())
        if client.stop.wait(delay) or client.stop.is_set():
            raise ScanStoppedError
        _admit(client)
        next_candidate = client.clock() + _CANDIDATE_INTERVAL
        candidate = snapshot["pending"][0]
        try:
            result = _candidate(
                client,
                candidate,
                snapshot["aliases"],
                prepared[candidate["pageid"]],
            )
        except RevisionUnavailableError:
            snapshot["unavailable"].append(candidate)
            snapshot["unavailable_count"] += 1
            result = None
        if result is not None:
            snapshot["items"] = [
                row
                for row in snapshot["items"]
                if row["talk_page_id"] != result["talk_page_id"]
            ]
            snapshot["items"].append(result)
        snapshot["checked"].append(candidate["pageid"])
        snapshot["checked_count"] += 1
        snapshot["pending"].pop(0)
        prepared.pop(candidate["pageid"])
        snapshot["status"] = "scanning"
        snapshot["error"] = None
        _save(path, snapshot)
    raise ScanStoppedError


def _refresh_completed(client: ScanClient, path: Path, snapshot: dict) -> None:
    """Remove restored findings after one complete daily source check.

    The historical cursor and checked IDs remain intact. A failed query
    preserves every prior finding and can be retried by the worker.
    """
    restored = set()
    for batch in batched(snapshot["items"], _BATCH, strict=False):
        _admit(client)
        assessed = page_projects(
            client.site,
            [row["page_id"] for row in batch],
            ["电子游戏"],
        )
        restored.update(assessed["page_id"].to_list())
    snapshot["items"] = [
        row for row in snapshot["items"] if row["page_id"] not in restored
    ]
    snapshot["refreshed_at"] = _now()
    snapshot["error"] = None
    _save(path, snapshot)


def run_scan(  # ruff: ignore[complex-structure]
    stop: Event,
    path: Path = records.DEFAULT_PATH,
    *,
    before_request: Callable[[], None] | None = None,
) -> None:
    """Run one resumable pass with a lock and cooperative cancellation.

    Candidate checks start at least 1.2 seconds apart; slow responses
    and history reads can reduce throughput below fifty pages a minute.
    API attempts are serial and at least 0.2 seconds apart. Failed
    candidates retain their queue position for a later retry. Completed
    checkpoints recheck findings once a UTC day, excluding pages now
    assessed by the project without repeating the historical sweep.

    Args:
        stop: Runtime cancellation signal, also interrupts pacing waits.
        path: Durable scan checkpoint outside immutable source.
        before_request: Optional owner admission before source attempts.

    Raises:
        OSError: Storage, locking or a source request failed.
        RuntimeError: Source coverage is unavailable or malformed.
        ValueError: Saved evidence is corrupt or exceeds its bounds.
    """  # ruff: ignore[docstring-extraneous-exception]
    if stop.is_set():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o640,
    )
    with os.fdopen(descriptor, "r+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            try:
                snapshot = records.read(path)
            except FileNotFoundError:
                snapshot = records.initial()
            completed = snapshot["status"] == "complete"
            refreshed = snapshot.get("refreshed_at", snapshot["updated_at"])
            if (
                completed
                and refreshed is not None
                and (
                    dt.datetime.fromisoformat(refreshed).date()
                    >= dt.datetime.now(dt.UTC).date()
                )
            ):
                return
            if not completed:
                snapshot["status"] = (
                    "scanning" if snapshot["pending"] else "discovering"
                )
            snapshot["error"] = None
            if completed:
                snapshot["refreshed_at"] = refreshed
            else:
                _save(path, snapshot)

            def backoff(value: str | None) -> None:
                """Retain source backoff across shutdown and restart."""
                snapshot["retry_after_at"] = value
                _save(path, snapshot)

            client = ScanClient(
                stop,
                retry_after_at=snapshot["retry_after_at"],
                on_backoff=backoff,
                before_request=before_request,
            )
            try:
                if completed:
                    _refresh_completed(client, path, snapshot)
                else:
                    _run(client, path, snapshot)
            except ScanStoppedError:
                snapshot["status"] = "complete" if completed else "interrupted"
                snapshot["error"] = None
                _save(path, snapshot)
            except Exception:
                snapshot["status"] = "complete" if completed else "error"
                snapshot["error"] = (
                    "A source observation failed; "
                    "the same candidate will be retried."
                )
                _save(path, snapshot)
                raise
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
