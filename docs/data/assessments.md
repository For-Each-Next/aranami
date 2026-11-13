# Assessment profiles

Assessment values belong to a specific wiki and project. A page can have
different class and importance in another project. PageAssessments database
output is the source standard; stored `pa_class` and `pa_importance` retain their
original strings rather than being replaced by display codes.

`shared.assessment_profiles.assessment_profile(wiki, project)` selects an exact
project definition, then the site default, then lossless observed values. JSON
files under `src/shared/profiles/assessments/` contain the audited definitions.
Chinese defaults follow [page assessment](https://zh.wikipedia.org/wiki/Wikipedia:页面评级)
and English defaults follow [content assessment](https://en.wikipedia.org/wiki/Wikipedia:Content_assessment#Grades).
The historical Chinese `Wikipedia:通用評級` proposal is not the current guide.

Exact raw lookup (`class_label`, `importance_label`) differs from read-time
`resolve` and `code`, which accept declared aliases. `expression` performs the
same mapping in Polars. Unknown labels remain unchanged and unclassified; null
never becomes an invented grade. Site defaults do not redefine an explicit
project vocabulary.

## Video games on zhwiki

The `zhwiki-video-games:1` profile separates each value's database label, code,
long name and short name. For example:

| Database value | Code | Long name | Short name |
|---|---|---|---|
| 典范 | FA | 典范级 | 典范 |
| 特色列表 | FL | 特色列表级 | 特表 |
| 甲 | A | 甲级 | 甲 |
| 甲级列表 | AL | 甲级列表级 | 甲表 |
| 优良 | GA | 优良级 | 优良 |
| 乙上 | Bplus | 乙上级 | 乙上 |
| 乙 | B | 乙级 | 乙 |
| 乙级列表 | BL | 乙级列表级 | 乙表 |
| 丙 | C | 丙级 | 丙 |
| 丙级列表 | CL | 丙级列表级 | 丙表 |
| 初 | Start | 初级 | 初 |
| 列表 | List | 列表级 | 列表 |
| 小作品 | Stub | 小作品级 | 小级 |
| 小列表 | SL | 小列表级 | 小表 |
| 未评 | Unassessed | 未评级 | 未评 |

These are all supported **article-type grades**, in the user-defined order.
Do not import additional grades from another wiki's generic vocabulary.
`消歧义` and `同类索引` are non-article types even in namespace 0. The other
observed non-article labels are `草稿`, `文件`, `分类`, `模板`, `主题`, `专题`,
`重定向` and `非条目`. Namespace, assessment type and actual redirect flag
are separate facts; mainspace alone is insufficient to establish an article.

The importance profile contains `极高`, `高`, `中`, `低`, `未知`, `不适用`,
with corresponding codes `Top`, `High`, `Mid`, `Low`, `Unknown`, `NA`.
Long names add `重要度`; short names retain the raw labels. Unknown observations
remain null and are distinct from the observed label `未知`.

A live replica audit at **2026-11-02 14:47:05 UTC** observed 25,158 project rows,
23 distinct raw class labels and six importance labels. The supported `甲` and
`甲级列表` values had no rows in that capture. The article vocabulary and order
above are the explicit project contract, not a list inferred only from that
one day's nonzero counts.

`/api/config` exposes structured profile records for consumers. An unaudited
taskforce uses its site default, never the parent project's override. Sites
without an audited profile use observed-only declarations. Chinese site defaults
include grades such as 丁 and 小小作品; their availability does not add them to
the Video games project's supported article-grade declaration. English empty
class is recorded as an empty string and displayed as unassessed. Unrecognized
project values such as `YStart` remain unchanged.

## Storage and display

`encode_assessments(frame, profile=...)` applies the chosen source declaration
to class/importance columns before writing Parquet Enum. It appends every
observed extra label without converting values. `decode_assessments` restores
String columns at the reader boundary. Decode independently encoded tables
before joins. Null, empty string and each explicit label remain distinct.
Unscoped or derived report tables use only their observed labels. There is no
global class or importance vocabulary combining different languages; raw source
tables select their wiki/project definition explicitly.

Daily manifests retain the profile identity and actual stored Enum declarations,
including extras. Readers validate metadata against the file rather than using
a later profile revision to reinterpret an older observation. Polars 2 reads
Parquet Enum as String; the reader restores only validated saved declarations
before integrity checks, preserving older files and their labels. Older generic
report tables remain readable; their storage declaration does not make English
labels the zhwiki source standard.

Display codes, long/short names, ordering and colors come from the selected
profile. `color` defines the grade's audited default; named `color_variants`
preserve established chart styles where they differ. `profile.color(...)` and
Polars `expression(..., field="color")` use the same metadata. No renderer keeps
its own grade-color inventory. Unverified colors remain null.

They do not overwrite raw facts. Aggregation must preserve article/list source
grades even when a newsletter intentionally groups A/AL or B/BL for presentation.
A classification or redirect change is not automatically a new article,
promotion date or deletion event. [Saved data](storage.md) defines snapshot
identity, date and observation semantics.

## Literal B/BL listing evidence

The listing-time collector reads explicit Video games classes and the verified
page-header banner-shell inheritance rule. A nonblank project class takes
precedence; missing or blank values may inherit one literal header shell class.
The shell can wrap the project banner or appear separately in the header.
Ambiguous, dynamic and unsupported evidence remains unknown. Automatic subject
classifications are not reconstructed from present-day page state.

The contract is pinned to Video games banner/class revisions
[92848131](https://zh.wikipedia.org/w/index.php?oldid=92848131) and
[92848137](https://zh.wikipedia.org/w/index.php?oldid=92848137), WPBannerMeta
[91444890](https://zh.wikipedia.org/w/index.php?oldid=91444890), banner shell
[95284439](https://zh.wikipedia.org/w/index.php?oldid=95284439), and PJBSClass
modules [95173400](https://zh.wikipedia.org/w/index.php?oldid=95173400) and
[95173398](https://zh.wikipedia.org/w/index.php?oldid=95173398).

Parquet retains exact talk heads, parser identities, completed ranges and
resumable parent cursors. Unchanged unsupported heads are not downloaded again;
a talk, class or parser change reconsiders them. Transient request failures and
budget deferrals remain retryable. The nearby article revision at or before the
assessment supplies the attribution cutoff and displayed B-Class date.
