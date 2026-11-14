"""Pre-render saved article-statistics presets after each data update.

Runtime calls this background output step after metadata is committed.
HTTP handlers only read the resulting Parquet pages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from data.articles import quality_pages
from data.articles.current_quality import (
    current_quality_identity,
    read_current_quality,
)
from data.runtime.status import read_status
from shared.config import routine_configuration
from shared.deployment import project_version
from webservice import pages
from webservice.presentation import local_url

_LANGUAGES = {"en": "English", "zh-hans": "简体中文", "zh-hant": "繁體中文"}


def prepare_quality_pages(runtime: Path) -> None:
    """Render all default presets from one complete saved generation.

    Each file replaces its predecessor only after successful rendering.
    Source acquisition and calculations have already finished.
    """
    source = current_quality_identity(runtime)
    if source == (None,):
        return
    status = read_status(runtime)
    config = {
        "version": project_version(),
        "routines": routine_configuration(),
    }
    for basis in ("current", "listed"):
        for include_b, all_grades in (
            (False, False),
            (True, False),
            (False, True),
        ):
            data = read_current_quality(
                runtime,
                include_b=include_b,
                authorship_basis=basis,
                all_grades=all_grades,
            )
            query = {"count_b": "1"} if include_b else {}
            if all_grades:
                query["all_grades"] = "1"
            if basis == "listed":
                query["listing_authors"] = "1"
            for locale in _LANGUAGES:
                navigation = {
                    "path": "/quality-analysis/",
                    "languages": [
                        {
                            "label": label,
                            "tag": {
                                "zh-hans": "zh-Hans",
                                "zh-hant": "zh-Hant",
                            }.get(language, language),
                            "url": local_url(
                                "/quality-analysis/",
                                **query,
                                lang=language,
                            ),
                            "current": language == locale,
                        }
                        for language, label in _LANGUAGES.items()
                    ],
                }
                content = pages.render_quality_analysis(
                    data,
                    status,
                    config,
                    locale,
                    navigation=navigation,
                )
                if current_quality_identity(runtime) != source:
                    return
                quality_pages.save(
                    quality_pages.path_for(
                        runtime,
                        locale,
                        include_b=include_b,
                        basis=basis,
                        all_grades=all_grades,
                    ),
                    content,
                    source,
                )
