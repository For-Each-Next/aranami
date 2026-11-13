"""Verify revision-pinned rendered comments without network requests."""

# Exact protocol parameters and bounds are regression contracts.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion, ambiguous-unicode-character-string]

from unittest import TestCase
from unittest.mock import Mock, patch

from shared.sources.discussions import read_comments
from shared.support.activity import activity_observer


def _comment(html: str, *, identifier: str = "c-opening") -> dict:
    """Build one detached rendered-comment API record.

    Returns:
        HTML and exact comment identity, author and UTC signature time.
    """
    return {
        "id": identifier,
        "type": "comment",
        "author": "MintCandy",
        "timestamp": "2026-10-28T17:47:00Z",
        "html": html,
    }


class TestRenderedComments(TestCase):
    """Keep display text tied to its requested source revision."""

    def setUp(self) -> None:
        """Create a read-only source with detached response fixtures."""
        self.site = Mock()
        self.site.dbName.return_value = "zhwiki"
        self.payload = {"discussiontoolspageinfo": {"threaditemshtml": []}}
        self.site.simple_request.return_value.submit.side_effect = lambda: (
            self.payload
        )

    def comments(self, *items: dict) -> tuple:
        """Read detached thread items through the normal adapter.

        Returns:
            Typed rendered bodies at the selected immutable oldid.
        """
        self.payload["discussiontoolspageinfo"]["threaditemshtml"] = [
            {"id": "h-topic", "replies": list(items)},
        ]
        return read_comments(self.site, 123)

    def test_named_external_links_and_expanded_templates_keep_visible_text(
        self,
    ) -> None:
        """Preserve rendered labels, expanded templates and entities."""
        rows = self.comments(
            _comment(
                '<p>可使用<a rel="mw:ExtLink" href="https://example.test/">'
                '輔助工具</a>，並參考<span typeof="mw:Transclusion">'
                "<b>已展開模板</b></span>。&lt;文字&gt; &amp; 其他內容。</p>",
            ),
        )
        assert (
            rows[0].text
            == "可使用輔助工具，並參考已展開模板。<文字> & 其他內容。"
        )
        assert "[" not in rows[0].text
        assert "https://" not in rows[0].text
        assert rows[0].revision_id == 123
        assert rows[0].comment_id == "c-opening"
        assert rows[0].timestamp == "2026-10-28T17:47:00+00:00"

    def test_media_hidden_elements_and_references_are_not_body_prose(
        self,
    ) -> None:
        """Omit rendered images, hidden content and UI references."""
        rows = self.comments(
            _comment(
                '<p>Visible<img alt="Internet-group-chat.svg|48px" '
                'src="x"> text.'
                '<figure><img src="x"><figcaption>Media caption</figcaption>'
                "</figure>"
                "<span hidden>hidden</span>"
                '<span aria-hidden="true">hidden</span>'
                '<span style="display: none !important">hidden</span>'
                '<span style="visibility:hidden">hidden</span>'
                '<sup class="reference">[1]</sup><script>untrusted()</script>'
                "<style>.hidden{}</style>"
                '<span typeof="mw:File">image label</span>'
                "</p><p>Second paragraph.</p>",
            ),
        )
        assert rows[0].text == "Visible text. Second paragraph."

    def test_mathml_uses_visible_math_without_source_annotations(self) -> None:
        """Preserve visible formula text without raw TeX metadata."""
        rows = self.comments(
            _comment(
                "<p>Formula <math><semantics><mi>x</mi>"
                '<annotation encoding="application/x-tex">'
                r"{\displaystyle x}</annotation>"
                '<annotation-xml encoding="MathML-Content">'
                "<ci>duplicate</ci></annotation-xml>"
                "</semantics></math> follows.</p>",
            ),
        )
        assert rows[0].text == "Formula x follows."

    def test_opening_and_later_replies_remain_separate(self) -> None:
        """Never concatenate another comment into the opening body."""
        first = _comment("<p>Opening only.</p>")
        first["replies"] = [
            _comment(
                "<p>Later reply.</p>",
                identifier="c-reply",
            ),
        ]
        rows = self.comments(first)
        assert {row.comment_id: row.text for row in rows} == {
            "c-opening": "Opening only.",
            "c-reply": "Later reply.",
        }

    def test_request_is_pinned_and_observability_names_rendered_comments(
        self,
    ) -> None:
        """Request signature-free HTML only at the selected oldid."""
        observed = []
        with activity_observer(
            lambda kind, source: observed.append((kind, source)),
        ):
            self.comments(_comment("<p>Body.</p>"))
        self.site.simple_request.assert_called_once_with(
            action="discussiontoolspageinfo",
            oldid=123,
            prop="threaditemshtml",
            threaditemsflags="excludesignatures",
            formatversion=2,
        )
        assert observed[0][1]["dataset"] == "discussion_comments"
        assert observed[0][1]["references"] == [
            {"kind": "revision", "value": "123"},
        ]

    def test_repeated_subheadings_keep_one_comment_each(self) -> None:
        """Collapse roots repeating nested subheading comments."""
        opening = _comment("<p>Opening only.</p>")
        reply = _comment("<p>Later reply.</p>", identifier="c-reply")
        subheading = {"id": "h-subtopic", "replies": [reply]}
        opening["replies"] = [subheading]
        self.payload["discussiontoolspageinfo"]["threaditemshtml"] = [
            {"id": "h-topic", "replies": [opening]},
            subheading,
        ]
        rows = read_comments(self.site, 123)
        assert len(rows) == 2
        assert {row.comment_id: row.text for row in rows} == {
            "c-opening": "Opening only.",
            "c-reply": "Later reply.",
        }

    def test_conflicting_ids_leave_only_affected_comments_unknown(
        self,
    ) -> None:
        """Omit conflicting IDs while retaining other comments."""
        rows = self.comments(
            _comment("<p>One.</p>"),
            _comment("<p>Different.</p>"),
            _comment("<p>One.</p>"),
            _comment("<p>Independent.</p>", identifier="c-independent"),
        )
        assert len(rows) == 1
        assert rows[0].comment_id == "c-independent"
        assert rows[0].text == "Independent."

    def test_refused_or_mismatched_revisions_never_fall_back(self) -> None:
        """Reject errors and conflicting revision provenance."""
        for payload in (
            {"error": {"code": "nosuchrevid"}},
            {"discussiontoolspageinfo": {"revid": 124, "threaditemshtml": []}},
            {"oldid": 124, "discussiontoolspageinfo": {"threaditemshtml": []}},
        ):
            self.payload = payload
            self.site.simple_request.reset_mock()
            with self.assertRaises(ValueError):
                read_comments(self.site, 123)
            assert self.site.simple_request.call_count == 1

    def test_invalid_ids_and_bounded_html_fail_before_acceptance(self) -> None:
        """Reject unsafe revision IDs and oversized rendered bodies."""
        for revision in (None, True, 0, -1, "123"):
            with self.assertRaises(ValueError):
                read_comments(self.site, revision)
        self.site.simple_request.assert_not_called()
        with (
            patch("shared.sources.discussions._MAX_HTML_BYTES", 5),
            self.assertRaises(ValueError),
        ):
            self.comments(_comment("<p>Too long.</p>"))

    def test_empty_rendered_thread_is_explicit_empty(self) -> None:
        """Keep valid empty coverage distinct from a failed read."""
        assert read_comments(self.site, 123) == ()
