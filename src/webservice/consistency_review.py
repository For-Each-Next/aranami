"""Link saved lead corrections to a source-editor userscript handoff.

Only an exact saved repair ID enters the Wikipedia URL. The optional
Tampermonkey script reads the ready correction through Aranami's query
API, checks the native edit form and opens a diff without saving.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlencode

if TYPE_CHECKING:
    from webservice.presentation import Interface


def repair_links(repair: dict | None, interface: Interface) -> list[dict]:
    """Link a ready correction to the current talk-page source editor.

    Args:
        repair: Complete validated saved correction, when ready.
        interface: Language for the visible changes action.

    Returns:
        One diff handoff link or no action without complete saved code.
    """
    if repair is None:
        return []
    return [
        {
            "url": "https://zh.wikipedia.org/w/index.php?"
            + urlencode({
                "title": repair["title"],
                "action": "edit",
                "section": "0",
                "aranami-repair": repair["id"],
            }),
            "label": interface.text("consistency.review_diff"),
        },
    ]
