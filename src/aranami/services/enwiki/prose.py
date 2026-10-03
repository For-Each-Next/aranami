"""Configure English Wikipedia article prose extraction.

Use ``PROFILE`` with the shared wikitext extractor for English articles.
Accepted translated headings and imported episode templates retain the
existing analysis behavior.
"""

import re

from aranami.support.prose import ProseProfile

PROFILE = ProseProfile(
    non_prose_headings=frozenset({
        "bibliography",
        "citations",
        "external links",
        "further reading",
        "notes",
        "references",
        "see also",
        "sources",
        "参考资料",
        "參考資料",
        "参考文献",
        "參考文獻",
        "注释",
        "註釋",
        "注解",
        "註解",
        "外部链接",
        "外部連結",
        "外部連接",
        "参见",
        "參見",
        "相关条目",
        "相關條目",
        "延伸阅读",
        "延伸閱讀",
        "来源",
        "來源",
    }),
    prose_templates=frozenset({
        "episode list",
        "episode list/sublist",
        "episode table/part",
        "剧集列表",
        "劇集列表",
        "分集列表",
    }),
    prose_parameters=re.compile(
        r"^(?:shortsummary|summary|episodesummary|description|plot|"
        r"简介|簡介|剧情|劇情|梗概|摘要)\d*$",
        flags=re.IGNORECASE,
    ),
)
