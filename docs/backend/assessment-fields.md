# Assessment field storage

Assessment storage preserves the exact class and importance labels returned by
sources. Use `shared.support.assessment_encoding.encode_assessments` before
writing a report table to Parquet and `decode_assessments` at its reader boundary
to retain the existing String-based source and report APIs.

Daily project assessment files apply the same encoding to their compact
`page_id`, `pa_class`, and `pa_importance` table. Their readers validate the
saved column declarations when recorded, then return the original String
schema. Earlier String files remain readable without being rewritten.

The encoder handles String columns named `class` or `importance`, and names
ending in `_class` or `_importance`. It gives each column a Polars string Enum
declaration. Other columns and types remain unchanged. The decoder only casts
assessment Enum columns back to String; existing String files remain readable.

## Declared labels

The class vocabulary follows the first four groups in Chinese Wikipedia's
[assessment statistics](https://zh.wikipedia.org/wiki/Wikipedia:页面评级#統計).
The fifth, deprecated group is not part of the standard declaration.

| Group | Labels |
|---|---|
| Articles | `FA`, `A`, `GA`, `Bplus`, `B`, `C`, `D`, `Start`, `Stub`, `Substub` |
| Lists and unassessed pages | `FL`, `AL`, `BL`, `CL`, `List`, `SL`, `Unassessed` |
| Other content | `Disambig`, `Setindex`, `Draft`, `FP`, `File`, `Category`, `Template`, `Module`, `Portal`, `Project` |
| Other pages | `User`, `Help`, `MediaWiki`, `Sandbox`, `Redirect`, `NA` |

Importance follows the same page's
[project importance scale](https://zh.wikipedia.org/wiki/Wikipedia:页面评级#专题重要度评级):
`极高`, `高`, `中`, `低`, `极低`, `不适用`, and `无`.
The declaration also includes the source codes `Top`, `High`, `Mid`, `Low`,
`Bottom`, and `NA`, preserving English source values without conversion.

Each Enum has a fixed declaration when constructed. Before casting, the encoder
extends that declaration with every distinct extra label observed in that column,
in sorted order. This preserves deprecated values, spelling variants, localized
labels, empty strings, and future source values without reinterpretation. Null
stays null and remains distinct from `无`, `NA`, `Unassessed`, and empty strings.

The declaration order is a storage convention, not a quality ranking. Decode
before joining independently encoded frames or applying existing String-based
filters. Parquet retains string values and the Enum declaration in its schema;
catalogues must inspect the actual saved schema, including observed extra labels.
Enums provide categorical in-memory representation. They do not promise smaller
Parquet files or faster end-to-end report processing than ordinary strings.
