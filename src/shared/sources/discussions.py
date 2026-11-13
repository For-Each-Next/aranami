"""Read rendered comment bodies from pinned wiki revisions.

DiscussionTools supplies each comment's rendered HTML separately, with
signatures excluded. This adapter returns visible plain text without
fetching current-page or snippet renderings as a fallback.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import TYPE_CHECKING

from shared.support.activity import report_activity

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

_MAX_HTML_BYTES = 8 * 1024 * 1024
_MAX_ITEMS = 10_000
_MAX_DEPTH = 256
_MAX_ID_BYTES = 4096
_MAX_ACCOUNT_BYTES = 1000
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_VOID = frozenset({
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
})
_OMIT = frozenset({
    "annotation",
    "annotation-xml",
    "audio",
    "figure",
    "gallery",
    "iframe",
    "img",
    "noscript",
    "script",
    "style",
    "video",
})
_BLOCK = frozenset({
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
})
_OMIT_CLASSES = frozenset({
    "reference",
    "references",
    "mw-editsection",
    "mw-ref",
    "mw-reflink-text",
    "mw-file-element",
    "mw-file-description",
})


@dataclass(frozen=True)
class RenderedComment:
    """Carry one rendered comment and its immutable source identity."""

    revision_id: int
    comment_id: str
    author: str
    timestamp: str
    text: str


class _VisibleText(HTMLParser):
    """Keep visible body text without media or hidden markup."""

    def __init__(self) -> None:
        """Prepare a detached HTML fragment parser."""
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, bool]] = []
        self._parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Track hidden/media ancestry and visible block separators.

        Raises:
            ValueError: The HTML ancestry exceeds its depth bound.
        """
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        style = re.sub(r"\s+", "", values.get("style") or "").casefold()
        blocked = (
            bool(self._stack and self._stack[-1][1])
            or tag in _OMIT
            or bool(classes & _OMIT_CLASSES)
            or "hidden" in values
            or (values.get("aria-hidden") or "").casefold() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
            or "mw:File" in (values.get("typeof") or "")
        )
        if not blocked and tag in _BLOCK:
            self._parts.append(" ")
        if tag not in _VOID:
            if len(self._stack) >= _MAX_DEPTH:
                message = "Rendered comment HTML exceeds its depth bound."
                raise ValueError(message)
            self._stack.append((tag, blocked))

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Handle self-closing elements without retaining ancestry."""
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        """Close matching ancestry and separate visible blocks."""
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                blocked = self._stack[index][1]
                del self._stack[index:]
                if not blocked and tag in _BLOCK:
                    self._parts.append(" ")
                break

    def handle_data(self, data: str) -> None:
        """Retain decoded text only from visible nodes."""
        if not self._stack or not self._stack[-1][1]:
            self._parts.append(data)

    def text(self) -> str:
        """Return rendered visible text with normalized whitespace.

        Returns:
            Link labels and expanded template text without HTML markup.
        """
        return " ".join("".join(self._parts).split())


def _visible_text(html: str) -> str:
    """Convert one already rendered comment body to plain visible text.

    Returns:
        Visible text, preserving inline labels without exposing markup.
    """
    parser = _VisibleText()
    parser.feed(html)
    parser.close()
    return parser.text()


def _public_text(value: object, limit: int) -> bool:
    """Check a bounded printable source identity.

    Returns:
        Whether a nonempty string stays within its encoded byte limit.
    """
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value.encode()) <= limit
        and _CONTROL.search(value) is None
    )


def _comment(item: dict, revision_id: int) -> RenderedComment:
    """Validate one comment identity and its rendered body.

    Returns:
        Exact comment metadata with UTC signature time and visible text.

    Raises:
        ValueError: Identity, timestamp or rendered HTML is malformed.
    """
    identity, author, stamp, html = (
        item.get("id"),
        item.get("author"),
        item.get("timestamp"),
        item.get("html"),
    )
    if (
        not _public_text(identity, _MAX_ID_BYTES)
        or not identity.startswith("c-")
        or not _public_text(author, _MAX_ACCOUNT_BYTES)
        or not _public_text(stamp, 64)
        or not isinstance(html, str)
    ):
        message = "Rendered comment identity or body is malformed."
        raise ValueError(message)
    instant = dt.datetime.fromisoformat(stamp)
    if instant.utcoffset() != dt.timedelta() or instant.microsecond:
        message = "Rendered comment timestamp is not an exact UTC second."
        raise ValueError(message)
    return RenderedComment(
        revision_id,
        identity,
        author,
        instant.isoformat(timespec="seconds"),
        _visible_text(html),
    )


def read_comments(
    site: BaseSite,
    revision_id: int,
) -> tuple[RenderedComment, ...]:
    """Read signature-free rendered comments at one immutable revision.

    The DiscussionTools API renders the requested oldid with Parsoid and
    returns a separate HTML body per comment. Its `excludesignatures`
    flag removes signature content before plain-text conversion.
    Verified against ApiDiscussionToolsPageInfo.php on 9 November 2026:
    https://github.com/wikimedia/mediawiki-extensions-DiscussionTools

    Args:
        site: Existing read-only wiki client.
        revision_id: Positive source revision selected by the caller.

    Returns:
        Rendered bodies and verified comment identities. A valid empty
        discussion returns an empty tuple. Repeated identical comments
        are collapsed; conflicting observations remain unknown.

    Raises:
        ValueError: Revision identity, response, or bounds are invalid.
        TypeError: A response contains malformed structural fields.
    """
    if type(revision_id) is not int or revision_id <= 0:
        message = "Rendered comments require an explicit source revision."
        raise ValueError(message)
    report_activity(
        "mediawiki",
        "discussion_comments",
        wiki=site.dbName(),
        references=[("revision", revision_id)],
    )
    # Pywikibot has no high-level API for these revision-pinned bodies.
    response = site.simple_request(
        action="discussiontoolspageinfo",
        oldid=revision_id,
        prop="threaditemshtml",
        threaditemsflags="excludesignatures",
        formatversion=2,
    ).submit()
    try:
        threads = _thread_list(response, revision_id)
        return _decode_threads(threads, revision_id)
    except (TypeError, ValueError) as error:
        error.add_note(f"DiscussionTools source revision: {revision_id}.")
        raise


def _thread_list(response: object, revision_id: int) -> list[dict]:
    """Validate a revision-pinned thread envelope.

    Returns:
        The complete source thread list.

    Raises:
        ValueError: The API failed or reports a different revision.
        TypeError: The response omits its structured thread list.
    """
    result = (
        response.get("discussiontoolspageinfo")
        if isinstance(response, dict)
        else None
    )
    if (
        not isinstance(response, dict)
        or "error" in response
        or not isinstance(result, dict)
    ):
        message = "Rendered discussion source returned no complete result."
        raise ValueError(message)
    for envelope in (response, result):
        if any(
            type(envelope[key]) is not int or envelope[key] != revision_id
            for key in ("revid", "oldid")
            if key in envelope
        ):
            message = "Rendered discussion source revision does not match."
            raise ValueError(message)
    threads = result.get("threaditemshtml")
    if not isinstance(threads, list):
        message = "Rendered discussion source has no thread list."
        raise TypeError(message)
    return threads


def _decode_threads(
    threads: list[dict],
    revision_id: int,
) -> tuple[RenderedComment, ...]:
    """Validate bounded thread items and collect their rendered bodies.

    Returns:
        Comment bodies with immutable revision and signature identities.
        Tree and flat-list repetitions are collapsed. Conflicting IDs
        are omitted without discarding independently verified comments.

    Raises:
        ValueError: Item identity, body, depth or size violates a bound.
        TypeError: A reply list is malformed.
    """
    stack = [(item, 0) for item in threads]
    comments = []
    seen = {}
    ambiguous = set()
    nodes = 0
    total = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if (
            not isinstance(item, dict)
            or nodes > _MAX_ITEMS
            or depth > _MAX_DEPTH
        ):
            message = "Rendered discussion source exceeds its item bounds."
            raise ValueError(message)
        html = item.get("html")
        if html is not None:
            if not isinstance(html, str):
                message = "Rendered thread HTML is malformed."
                raise ValueError(message)
            total += len(html.encode())
            if total > _MAX_HTML_BYTES:
                message = "Rendered discussion HTML exceeds its byte bound."
                raise ValueError(message)
        if item.get("type") == "comment" or str(item.get("id", "")).startswith(
            "c-",
        ):
            comment = _comment(item, revision_id)
            previous = seen.get(comment.comment_id)
            if previous is None:
                seen[comment.comment_id] = comment
                comments.append(comment)
            elif previous != comment:
                ambiguous.add(comment.comment_id)
        replies = item.get("replies", [])
        if not isinstance(replies, list):
            message = "Rendered thread replies are malformed."
            raise TypeError(message)
        stack.extend((reply, depth + 1) for reply in reversed(replies))
    return tuple(
        comment for comment in comments if comment.comment_id not in ambiguous
    )
