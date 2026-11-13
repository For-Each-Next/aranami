"""Look up bounded, resumable recognition evidence without writing data.

Each pass inspects at most eight own-talk subpages. The first pass also
reads the recorded talk revision, current talk, and snapshots two and
thirty days after listing. These are sampled snapshots, not
an exhaustive revision scan. Unknown evidence remains unknown.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import TYPE_CHECKING

import pywikibot

from shared.sources import article_milestones as source
from tasks.zhwiki.article_milestones import (
    extract_credits,
    nomination_targets,
    source_events,
)

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

_MAX_CURSOR_BYTES = 2048
_MAX_LINKED_TARGETS = 2
_PARSER_CONTRACT = "zh-recognition-sources:multiple-history-templates:1"


def _version_cursor(result: dict) -> dict:
    """Bind resumable parsed candidates to their extraction contract.

    Returns:
        The result with a bounded versioned cursor; finished lookups
        retain their existing empty marker and durable proof fields.
    """
    cursor = result.get("lookup_cursor")
    if not cursor:
        return result
    state = json.loads(cursor)
    state["parser_contract"] = _PARSER_CONTRACT
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode()) > _MAX_CURSOR_BYTES:
        return {
            **result,
            "status": "blocked",
            "reason": "continuation_too_large",
            "lookup_cursor": None,
        }
    return {**result, "lookup_cursor": encoded}


def _url(revision_id: int) -> str:
    """Build an exact source permalink.

    Returns:
        The revision's fixed Chinese Wikipedia URL.
    """
    return f"https://zh.wikipedia.org/w/index.php?oldid={revision_id}"


def _result(
    status: str,
    reason: str,
    cursor: str | None,
    **fields: object,
) -> dict:
    """Build an outcome that preserves unknown credits.

    Returns:
        A bounded lookup result for the durable record merger.
    """
    return {
        "status": status,
        "reason": reason,
        "lookup_cursor": cursor,
        **fields,
    }


def _remember(
    state: dict,
    revision: source.SourceRevision,
    candidates: list[dict],
) -> None:
    """Retain one candidate and exact proof across archive pages."""
    for candidate in candidates:
        prior = state.get("candidate")
        fields = ("nominator", "contributors", "main_author")
        ranked_fields = (*fields, "question")
        if prior is not None:
            if prior["identity"] != candidate["identity"] or any(
                prior.get(field) is not None
                and candidate.get(field) is not None
                and prior[field] != candidate[field]
                for field in fields
            ):
                state["ambiguous"] = True
                continue
            score = sum(
                candidate.get(field) is not None for field in ranked_fields
            )
            prior_score = sum(
                prior.get(field) is not None for field in ranked_fields
            )
            if score < prior_score or (
                score == prior_score
                and revision.revision_id <= state["proof"]["revision_id"]
            ):
                continue
        digest = hashlib.sha256(
            json.dumps(candidate, ensure_ascii=False, sort_keys=True).encode(),
        ).hexdigest()
        state.update(
            candidate={
                **candidate,
                "question": hashlib.sha256(
                    candidate["question"].encode(),
                ).hexdigest()
                if candidate.get("question")
                else None,
            },
            digest=digest,
            proof={
                "title": revision.title,
                "revision_id": revision.revision_id,
                "anchor": candidate.get("nomination_link"),
            },
        )


def _initial(
    site: BaseSite,
    talk: str,
    event: dict,
) -> list[source.SourceRevision]:
    """Read bounded historical anchors and the current discussion.

    Returns:
        Available exact snapshots, deduplicated by revision identity.
    """
    snapshots = []
    revision_id = event.get("revision_id")
    if isinstance(revision_id, int) and revision_id > 0:
        snapshots.append(
            source.read_revision(site, talk, revision_id=revision_id),
        )
    snapshots.append(source.read_revision(site, talk))
    day = event.get("date")
    if isinstance(day, dt.date):
        boundary = dt.datetime.combine(day, dt.time(), dt.UTC)
        snapshots.extend(
            source.read_revision(
                site,
                talk,
                at=boundary + dt.timedelta(days=days),
            )
            for days in (2, 30)
        )
    return list(
        {
            row.revision_id: row for row in snapshots if row is not None
        }.values(),
    )


def _linked(
    site: BaseSite,
    event: dict,
    snapshots: list[source.SourceRevision],
    state: dict,
) -> None:
    """Read up to two explicitly recorded nomination archive targets.

    More targets are ambiguous rather than silently truncating evidence.
    """
    targets = set().union(
        *(nomination_targets(row.text, event, site) for row in snapshots),
    )
    if len(targets) > _MAX_LINKED_TARGETS:
        state["ambiguous"] = True
        return
    for target in sorted(targets):
        title, _, anchor = target.partition("#")
        page = pywikibot.Page(site, title)
        if page.namespace() not in {1, 4} or page.site != site:
            continue
        revision = source.read_revision(site, page.title())
        if revision is not None:
            _remember(
                state,
                revision,
                extract_credits(
                    revision.text,
                    {**event, "nomination_link": "#" + anchor},
                    site,
                ),
            )


def _lookup(site: BaseSite, event: dict, state: dict) -> dict:
    """Advance one page and resolve unique nomination evidence.

    Returns:
        Credits, an unknown outcome, or a resumable continuation.
    """
    talk = pywikibot.Page(site, event["title"]).toggleTalkPage().title()
    snapshots = [] if state else _initial(site, talk, event)
    recovered = {}
    if event.get("date") is None:
        dates = {
            row["date"]
            for revision in snapshots
            for row in source_events(
                revision.text,
                site,
                event["title"],
                event.get("_aliases"),
            )
            if row.get("grade") == event.get("grade")
            and row.get("action") == "added"
            and row.get("date") is not None
        }
        if len(dates) != 1:
            return _result("unresolved", "missing_or_ambiguous_date", "")
        event = {**event, "date": dates.pop()}
        recovered["date"] = event["date"]
        recovered["sources"] = [
            {
                "title": row.title,
                "revision_id": row.revision_id,
                "url": _url(row.revision_id),
            }
            for row in snapshots
        ]
    if event.get("kind") == "dyk":
        previous = [
            row["date"]
            for revision in snapshots
            for row in source_events(
                revision.text,
                site,
                event["title"],
                event.get("_aliases"),
            )
            if row.get("kind") == "dyk"
            and row.get("date") is not None
            and row["date"] < event["date"]
        ]
        if previous:
            state["previous_day"] = max(previous).isoformat()
        if state.get("previous_day"):
            event = {
                **event,
                "previous_date": dt.date.fromisoformat(state["previous_day"]),
            }
    for revision in snapshots:
        _remember(state, revision, extract_credits(revision.text, event, site))
    archives, after = source.read_archives(
        site,
        talk,
        after=state.get("after"),
    )
    for revision in archives:
        _remember(state, revision, extract_credits(revision.text, event, site))
    _linked(site, event, snapshots + archives, state)
    if after:
        state["after"] = after
        cursor = json.dumps(state, ensure_ascii=False, sort_keys=True)
        if len(cursor.encode()) > _MAX_CURSOR_BYTES:
            return _result("blocked", "continuation_too_large", None)
        return _result(
            "pending",
            "talk_archives_remaining",
            cursor,
            **recovered,
        )
    return _finish(site, event, state, recovered)


def _finish(site: BaseSite, event: dict, state: dict, recovered: dict) -> dict:
    """Verify the unique retained proof after archive exhaustion.

    Returns:
        Exact evidence or an explicit unresolved outcome.
    """
    if state.get("ambiguous"):
        return _result("unresolved", "ambiguous_nomination", "", **recovered)
    proof = state.get("proof")
    if not proof:
        return _result(
            "unresolved",
            "sampled_talk_sources_exhausted",
            "",
            **recovered,
        )
    revision = source.read_revision(
        site,
        proof["title"],
        revision_id=proof["revision_id"],
    )
    if revision is None:
        return _result(
            "blocked",
            "proof_unavailable",
            event.get("lookup_cursor"),
        )
    candidates = extract_credits(
        revision.text,
        {
            **event,
            "nomination_link": proof.get("anchor")
            or event.get("nomination_link"),
        },
        site,
    )
    credit = next(
        (
            candidate
            for candidate in candidates
            if hashlib.sha256(
                json.dumps(
                    candidate,
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode(),
            ).hexdigest()
            == state["digest"]
        ),
        None,
    )
    if credit is None:
        return _result("unresolved", "proof_no_longer_matches", "")
    credit = {key: value for key, value in credit.items() if key != "identity"}
    credit["nomination_link"] = _url(revision.revision_id) + (
        credit.get("nomination_link") or ""
    )
    complete = bool(credit.get("nominator")) and (
        event.get("kind") != "dyk"
        or bool(credit.get("contributors") and credit.get("question"))
    )
    evidence = recovered.pop("sources", [])
    return _result(
        "complete" if complete else "unresolved",
        "explicit_credit" if complete else "explicit_fields_missing",
        "",
        sources=[
            *evidence,
            {
                "title": revision.title,
                "revision_id": revision.revision_id,
                "url": _url(revision.revision_id),
            },
        ],
        **recovered,
        **credit,
    )


def _cursor_state(cursor: str | None) -> tuple[dict, bool] | None:
    """Discard continuation facts from an older parser contract.

    Returns:
        Current resumable state and whether the input was reusable, or
        none when the cursor has unsupported fields.
    """
    state = json.loads(cursor) if cursor else {}
    if not isinstance(state, dict) or set(state) - {
        "after",
        "digest",
        "proof",
        "ambiguous",
        "candidate",
        "previous_day",
        "parser_contract",
    }:
        return None
    current = not state or state.get("parser_contract") == _PARSER_CONTRACT
    if not current:
        state = {}
    state.pop("parser_contract", None)
    return state, current


def lookup_milestone(site: BaseSite, event: dict) -> dict:
    """Advance one bounded lookup of historical nomination credits.

    Args:
        site: Existing runtime Chinese Wikipedia client.
        event: One durable event with its saved continuation.

    Returns:
        Credits with exact provenance, or a structured unresolved
        or pending result. Undated Bplus observations require Article
        history and make no source requests. No files are written here.
    """
    requires_history = (
        event.get("kind") == "quality"
        and event.get("grade") == "Bplus"
        and event.get("date") is None
    )
    cursor = event.get("lookup_cursor")
    if requires_history or (isinstance(cursor, str) and not cursor):
        return _result(
            "unresolved",
            "article_history_date_required"
            if requires_history
            else "source_search_finished",
            "",
        )
    if not event.get("title"):
        return _result("blocked", "missing_article_identity", "")
    if cursor is not None and (
        not isinstance(cursor, str) or len(cursor.encode()) > _MAX_CURSOR_BYTES
    ):
        return _result("blocked", "invalid_continuation", None)
    try:
        parsed = _cursor_state(cursor)
        if parsed is None:
            return _result("blocked", "invalid_continuation", cursor)
        state, current_contract = parsed
        result = _lookup(
            site,
            {
                **event,
                "_aliases": source.template_names(site),
                "lookup_cursor": cursor if current_contract else None,
            },
            state,
        )
        return _version_cursor(result)
    except (
        pywikibot.exceptions.Error,
        ValueError,
        OSError,
        TypeError,
        KeyError,
    ) as exc:
        reason = (
            "source_throttled"
            if getattr(exc, "code", None) in {"ratelimited", "maxlag"}
            else "source_unavailable"
        )
        return _result("blocked", reason, cursor)
