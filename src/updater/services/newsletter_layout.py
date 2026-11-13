"""Render issue links and source notices from saved layouts."""

# Preserve audited Chinese wikitext punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import mwparserfromhell

if TYPE_CHECKING:
    from shared.newsletter import Issue

_SOURCE_BANNER_SUFFIX = (
    "{{pb}}可於[[toolforge:aranami/newsletter/|此處]]參看"
    "自動生成的wikitext文字。"
)


def newsletter_source_banner(text: str) -> str:
    """Link the leading draft notice to its generated source.

    Args:
        text: Saved source with its original top-level draft banner.

    Returns:
        Source with the automatic-draft link appended once to the first
        mbox text. Other parameters, whitespace, and authored text stay
        intact; missing or nested-only banners leave source unchanged.
    """
    code = mwparserfromhell.parse(text)
    for template in code.filter_templates(recursive=False):
        if not template.name.matches("mbox"):
            continue
        if template.has("text"):
            parameter = template.get("text")
            value = str(parameter.value)
            if _SOURCE_BANNER_SUFFIX not in value:
                content = value.rstrip()
                parameter.value = (
                    content + _SOURCE_BANNER_SUFFIX + value[len(content) :]
                )
        break
    return str(code)


def issue_contents(text: str, issue: Issue) -> str:
    """Adapt the prepared contents to January's combined annual feature.

    Args:
        text: Validated main layout with issue metadata slots.
        issue: Destination publication month.

    Returns:
        The prepared layout, omitting January's separate interview link.
    """
    if issue.month != 1:
        return re.sub(
            r"(/簡訊/@@aranami:issue@@/專題\|)"
            r"專題報導兼訪談：\d{4}年年終回顧",
            r"\g<1>專題報導：<待定>",
            text,
        )
    text = re.sub(
        r"^\*[^\r\n]*?/簡訊/@@aranami:issue@@/訪談\|[^\r\n]*\r?\n",
        "",
        text,
        flags=re.MULTILINE,
    )
    return re.sub(
        r"(/簡訊/@@aranami:issue@@/專題\|)[^\]\r\n]*",
        rf"\g<1>專題報導兼訪談：{issue.year - 1}年年終回顧",
        text,
    )
