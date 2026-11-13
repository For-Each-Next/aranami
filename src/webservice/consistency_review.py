"""Link saved lead corrections to a source-editor userscript handoff.

Only an exact saved repair ID enters the Wikipedia URL. The optional
Tampermonkey script reads the ready correction through Aranami's query
API, checks the native edit form and opens a preview without saving.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

if TYPE_CHECKING:
    from webservice.presentation import Interface


def repair_links(repair: dict | None, interface: Interface) -> list[dict]:
    """Link a ready correction to the current talk-page source editor.

    Args:
        repair: Complete validated saved correction, when ready.
        interface: Language for the visible prefill action.

    Returns:
        One preview link, or no action without complete saved code.
    """
    if repair is None:
        return []
    return [
        {
            "url": "https://zh.wikipedia.org/wiki/"
            + quote(repair["title"].replace(" ", "_"), safe=":/")
            + "?"
            + urlencode({
                "action": "edit",
                "section": "0",
                "aranami-check-job": repair["id"],
            }),
            "label": interface.text("consistency.prefill"),
        },
    ]
