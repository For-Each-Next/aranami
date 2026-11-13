"""Retain quality metrics and precomputed web summaries in Parquet.

Background collectors checkpoint the full current cohort and bounded
measurement updates. HTTP readers load immutable selected generations
without creating locks or recomputing report statistics.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import polars as pl

from data.articles.storage import DEFAULT_ROOT
from data.collection_leases import collection_lease as _collection_lease
from data.reports import ReportResult, ReportStore
from shared.assessment_profiles import assessment_profile

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from data.reports import ReportBundle

GRADES = ("FA", "FL", "A", "AL", "GA", "Bplus")
_LENGTH_GRADES = ("FA", "GA", "Bplus")
_LENGTH_IMPORTANCE = ("Top", "High", "Mid", "Low")
STAMP = pl.Datetime("us", "UTC")
ARTICLE_SCHEMA = {
    "page_id": pl.Int64,
    "title": pl.String,
    "grade": pl.String,
    "importance": pl.String,
    "article_revision_id": pl.Int64,
    "talk_page_id": pl.Int64,
    "talk_revision_id": pl.Int64,
    "source_bytes": pl.Int64,
    "listed_date": pl.Date,
    "listed_oldid": pl.Int64,
    "observed_at": STAMP,
    "measurement_contract": pl.String,
    "prose_words": pl.Int64,
    "prose_bytes": pl.Int64,
    "measured_at": STAMP,
    "attempted_at": STAMP,
}
MEASURE_KEY = ["page_id", "article_revision_id", "measurement_contract"]
MEASURE_FIELDS = (
    "prose_words",
    "prose_bytes",
    "source_bytes",
    "measured_at",
    "attempted_at",
)
_QUANTILES = {
    "median_words": pl.Float64,
    "central_68_low": pl.Float64,
    "central_68_high": pl.Float64,
    "central_95_low": pl.Float64,
    "central_95_high": pl.Float64,
}
GRADE_SCHEMA = {
    "grade": pl.String,
    "pages": pl.Int64,
    "measured": pl.Int64,
    "dated": pl.Int64,
    **_QUANTILES,
}
IMPORTANCE_SCHEMA = {
    "importance": pl.String,
    "pages": pl.Int64,
    "measured": pl.Int64,
    "dated": pl.Int64,
}
_LEGACY_LENGTH_SCHEMA = {
    "grade": pl.String,
    "importance": pl.String,
    "pages": pl.Int64,
    "measured": pl.Int64,
    **_QUANTILES,
}
LENGTH_SCHEMA = {"group": pl.String, **_LEGACY_LENGTH_SCHEMA}
YEAR_SCHEMA = {"year": pl.Int32, "grade": pl.String, "pages": pl.Int64}
YEARS_SCHEMA = {
    "year": pl.Int32,
    "pages": pl.Int64,
    **dict.fromkeys(GRADES, pl.Int64),
}
COVERAGE_SCHEMA = {
    "total": pl.Int64,
    "measured": pl.Int64,
    "dated": pl.Int64,
    "median_words": pl.Float64,
}
_SCHEMAS = {
    "articles": ARTICLE_SCHEMA,
    "grade_totals": GRADE_SCHEMA,
    "importance_totals": IMPORTANCE_SCHEMA,
    "year_totals": YEAR_SCHEMA,
    "years": YEARS_SCHEMA,
    "lengths": LENGTH_SCHEMA,
    "coverage": COVERAGE_SCHEMA,
}
_KEY = "articles/quality-analysis/zhwiki-video-games"
_ROOT = DEFAULT_ROOT.parent
_MAX_ROWS = 10_000
_MAX_BYTES = 16 * 1024 * 1024


def collection_lease(runtime: Path = Path()) -> AbstractContextManager[bool]:
    """Admit one complete analysis pass without blocking a rival.

    Args:
        runtime: Runtime owner of the source collection and report.

    Returns:
        A context yielding whether this collector owns the source lease.
    """
    return _collection_lease(runtime, "quality-analysis")


def _validate_articles(frame: pl.DataFrame) -> None:
    """Reject partial identities and contradictory measurements.

    Raises:
        ValueError: Saved article observations violate their contract.
    """
    if (
        frame.schema != pl.Schema(ARTICLE_SCHEMA)
        or frame.height > _MAX_ROWS
        or frame.estimated_size() > _MAX_BYTES
        or frame["page_id"].n_unique() != frame.height
        or frame["observed_at"].n_unique() > 1
    ):
        message = "Invalid quality-analysis article schema or size."
        raise ValueError(message)
    required = (
        "page_id",
        "title",
        "grade",
        "importance",
        "article_revision_id",
        "observed_at",
        "measurement_contract",
    )
    invalid = frame.filter(
        pl.any_horizontal(pl.col(*required).is_null())
        | pl.any_horizontal(pl.col("page_id", "article_revision_id") <= 0)
        | ~pl.col("grade").is_in(GRADES)
        | ~pl.col("title").str.len_bytes().is_between(1, 4096)
        | (pl.col("measurement_contract").str.len_bytes() == 0)
        | pl.any_horizontal(
            pl.col("source_bytes", "prose_words", "prose_bytes") < 0,
        )
        | pl.any_horizontal(
            pl.col("talk_page_id", "talk_revision_id", "listed_oldid") <= 0,
        )
        | (pl.col("listed_date") > pl.col("observed_at").dt.date())
        | (pl.col("measured_at").is_null() != pl.col("prose_words").is_null())
        | (pl.col("prose_words").is_null() != pl.col("prose_bytes").is_null()),
    )
    if invalid.height:
        message = "Invalid quality-analysis identity or measurement."
        raise ValueError(message)


def _quantiles() -> list[pl.Expr]:
    """Build native summary expressions without Python row callbacks.

    Returns:
        Explicit median and central interval expressions.
    """
    return [
        pl.col("prose_words").median().alias("median_words"),
        *(
            pl
            .col("prose_words")
            .quantile(quantile, interpolation="linear")
            .alias(name)
            for name, quantile in (
                ("central_68_low", 0.16),
                ("central_68_high", 0.84),
                ("central_95_low", 0.025),
                ("central_95_high", 0.975),
            )
        ),
    ]


def _summaries(articles: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Precompute all web statistics at the background write boundary.

    Returns:
        Explicitly typed aggregate tables, including empty summaries.
    """
    profile = assessment_profile("zhwiki", "电子游戏")
    grade_order = profile.expression(
        "grade",
        "class",
        field="order",
        preserve_unknown=False,
    )
    importance_order = profile.expression(
        "importance",
        "importance",
        field="order",
        preserve_unknown=False,
    )
    articles = articles.sort(
        "listed_date",
        grade_order,
        "title",
        "page_id",
        descending=[True, False, False, False],
        nulls_last=True,
    )
    counts = [
        pl.len().alias("pages"),
        pl.col("prose_words").count().alias("measured"),
    ]
    grade_totals = (
        articles
        .group_by("grade")
        .agg(
            *counts,
            pl.col("listed_date").count().alias("dated"),
            *_quantiles(),
        )
        .cast(GRADE_SCHEMA)
        .sort(grade_order)
    )
    importance_totals = (
        articles
        .group_by("importance")
        .agg(*counts, pl.col("listed_date").count().alias("dated"))
        .cast(IMPORTANCE_SCHEMA)
        .sort(importance_order, nulls_last=True)
    )
    lengths = _length_summaries(articles)
    year_totals = (
        articles
        .filter(pl.col("listed_date").is_not_null())
        .with_columns(pl.col("listed_date").dt.year().alias("year"))
        .group_by("year", "grade")
        .agg(pl.len().alias("pages"))
        .cast(YEAR_SCHEMA)
        .sort("year", grade_order, descending=[True, False])
    )
    years = (
        year_totals
        .group_by("year")
        .agg(
            pl.col("pages").sum(),
            *(
                pl
                .col("pages")
                .filter(pl.col("grade") == grade)
                .sum()
                .alias(grade)
                for grade in GRADES
            ),
        )
        .cast(YEARS_SCHEMA)
        .sort("year", descending=True)
    )
    coverage = articles.select(
        pl.len().alias("total"),
        pl.col("prose_words").count().alias("measured"),
        pl.col("listed_date").count().alias("dated"),
        pl.col("prose_words").median().alias("median_words"),
    ).cast(COVERAGE_SCHEMA)
    return {
        "articles": articles,
        "grade_totals": grade_totals,
        "importance_totals": importance_totals,
        "year_totals": year_totals,
        "years": years,
        "lengths": lengths,
        "coverage": coverage,
    }


def _length_keys() -> pl.DataFrame:
    """Declare the seven prose comparisons independently of coverage.

    Returns:
        Grade and pooled-importance keys in their display order.
    """
    return pl.concat([
        pl.DataFrame({"grade": _LENGTH_GRADES}).select(
            pl.lit("grade").alias("group"),
            "grade",
            pl.lit(None, dtype=pl.String).alias("importance"),
        ),
        pl.DataFrame({"importance": _LENGTH_IMPORTANCE}).select(
            pl.lit("importance").alias("group"),
            pl.lit(None, dtype=pl.String).alias("grade"),
            "importance",
        ),
    ])


def _length_summaries(articles: pl.DataFrame) -> pl.DataFrame:
    """Pool prose measurements from FA, GA and Bplus articles only.

    Empty comparisons retain zero coverage and unknown intervals.
    Unknown importance remains in its grade comparison but does not
    enter the four known-importance comparisons.

    Returns:
        Seven precomputed grade or importance distributions.
    """
    eligible = articles.filter(pl.col("grade").is_in(_LENGTH_GRADES))
    counts = [
        pl.len().alias("pages"),
        pl.col("prose_words").count().alias("measured"),
    ]
    keys = _length_keys()
    summaries = [
        keys.filter(pl.col("group") == dimension).join(
            eligible.group_by(dimension).agg(*counts, *_quantiles()),
            on=dimension,
            how="left",
            maintain_order="left",
        )
        for dimension in ("grade", "importance")
    ]
    return (
        pl
        .concat(summaries)
        .with_columns(pl.col("pages", "measured").fill_null(0))
        .select(list(LENGTH_SCHEMA))
        .cast(LENGTH_SCHEMA)
    )


def _validate(tables: dict[str, pl.DataFrame]) -> None:
    """Validate bounded saved tables without recalculating aggregates.

    Raises:
        ValueError: A selected generation has an incompatible schema.
    """
    complete = set(_SCHEMAS)
    if set(tables) not in (complete, complete - {"importance_totals"}):
        message = "Quality analysis requires all precomputed tables."
        raise ValueError(message)
    for name, frame in tables.items():
        schema = _SCHEMAS[name]
        accepted = [pl.Schema(schema)]
        if name == "lengths":
            accepted.append(pl.Schema(_LEGACY_LENGTH_SCHEMA))
        if (
            frame.schema not in accepted
            or frame.height > _MAX_ROWS
            or frame.estimated_size() > _MAX_BYTES
        ):
            message = "Invalid quality-analysis summary schema or size."
            raise ValueError(message)
    _validate_articles(tables["articles"])
    if tables["coverage"].height != 1:
        message = "Quality analysis requires one coverage observation."
        raise ValueError(message)
    _validate_summaries(tables)


def _validate_summaries(tables: dict[str, pl.DataFrame]) -> None:
    """Reject contradictory counts and invalid precomputed intervals.

    Raises:
        ValueError: Saved summary facts contradict the complete cohort.
    """
    coverage = tables["coverage"]
    if coverage.filter(
        pl.any_horizontal(pl.col("total", "measured", "dated").is_null())
        | (pl.col("total") < 0)
        | ~pl.all_horizontal(
            pl.col("measured", "dated").is_between(0, pl.col("total")),
        )
        | ((pl.col("measured") == 0) != pl.col("median_words").is_null())
        | (~pl.col("median_words").is_finite()).fill_null(value=False)
        | (pl.col("median_words") < 0),
    ).height:
        message = "Invalid quality-analysis coverage facts."
        raise ValueError(message)
    observation = coverage.row(0, named=True)
    comparisons = [
        (observation["total"], tables["articles"].height),
        (observation["total"], tables["grade_totals"]["pages"].sum()),
        (observation["measured"], tables["grade_totals"]["measured"].sum()),
        (observation["dated"], tables["grade_totals"]["dated"].sum()),
        (observation["dated"], tables["year_totals"]["pages"].sum()),
        (observation["dated"], tables["years"]["pages"].sum()),
    ]
    if "importance_totals" in tables:
        importance = tables["importance_totals"]
        _validate_groups(importance, ["importance"])
        comparisons.extend([
            (observation["total"], importance["pages"].sum()),
            (observation["measured"], importance["measured"].sum()),
            (observation["dated"], importance["dated"].sum()),
        ])
    if any(actual != expected for actual, expected in comparisons):
        message = "Quality-analysis totals contradict their saved cohort."
        raise ValueError(message)
    for name, keys in (
        ("grade_totals", ["grade"]),
        ("year_totals", ["year", "grade"]),
        ("years", ["year"]),
    ):
        _validate_groups(tables[name], keys)
    lengths = tables["lengths"]
    if lengths.schema == pl.Schema(_LEGACY_LENGTH_SCHEMA):
        _validate_groups(lengths, ["grade", "importance"])
        if (
            lengths["pages"].sum() != observation["total"]
            or lengths["measured"].sum() != observation["measured"]
        ):
            message = "Legacy prose totals contradict their saved cohort."
            raise ValueError(message)
    else:
        _validate_lengths(lengths, tables["grade_totals"])
    for name in ("grade_totals", "lengths"):
        _validate_intervals(tables[name])


def _validate_lengths(
    frame: pl.DataFrame,
    grade_totals: pl.DataFrame,
) -> None:
    """Validate saved comparison keys and counts without new statistics.

    Raises:
        ValueError: Comparisons contain invalid keys or coverage counts.
    """
    keys = _length_keys()
    dimensions = keys.columns
    grades = frame.filter(pl.col("group") == "grade")
    importance = frame.filter(pl.col("group") == "importance")
    expected = grades.join(
        grade_totals.select(
            "grade",
            pl.col("pages").alias("expected_pages"),
            pl.col("measured").alias("expected_measured"),
        ),
        on="grade",
        how="left",
    )
    invalid_keys = (
        frame.height != keys.height
        or frame.select(pl.struct(dimensions).n_unique()).item() != keys.height
        or frame
        .select(dimensions)
        .join(keys, on=dimensions, how="anti", nulls_equal=True)
        .height
    )
    invalid_counts = (
        frame.filter(
            pl.any_horizontal(pl.col("pages", "measured").is_null())
            | (pl.col("pages") < 0),
        ).height
        or expected.filter(
            (pl.col("pages") != pl.col("expected_pages").fill_null(0))
            | (pl.col("measured") != pl.col("expected_measured").fill_null(0)),
        ).height
        or importance["pages"].sum() > grades["pages"].sum()
        or importance["measured"].sum() > grades["measured"].sum()
    )
    if invalid_keys or invalid_counts:
        message = "Invalid quality-analysis prose comparison coverage."
        raise ValueError(message)


def _validate_groups(frame: pl.DataFrame, keys: list[str]) -> None:
    """Check saved summary identities and complete nonnegative counts.

    Raises:
        ValueError: A saved aggregate has duplicate or invalid keys.
    """
    required = [name for name in frame.columns if name not in _QUANTILES]
    invalid = pl.any_horizontal(pl.col(*required).is_null()) | (
        pl.col("pages") <= 0
    )
    if "grade" in frame.columns:
        invalid |= ~pl.col("grade").is_in(GRADES)
    if "importance" in frame.columns:
        labels = assessment_profile("zhwiki", "电子游戏").labels("importance")
        invalid |= ~pl.col("importance").is_in([
            label.code for label in labels
        ])
    for observation in ("measured", "dated"):
        if observation in frame.columns:
            invalid |= ~pl.col(observation).is_between(0, pl.col("pages"))
    if "year" in frame.columns:
        invalid |= pl.col("year") < 1
    if all(grade in frame.columns for grade in GRADES):
        invalid = (
            invalid
            | pl.any_horizontal(pl.col(*GRADES) < 0)
            | (pl.sum_horizontal(pl.col(*GRADES)) != pl.col("pages"))
        )
    if (
        frame.filter(invalid).height
        or frame.select(
            pl.struct(keys).n_unique(),
        ).item()
        != frame.height
    ):
        message = "Invalid quality-analysis summary identity or count."
        raise ValueError(message)


def _validate_intervals(frame: pl.DataFrame) -> None:
    """Check finite, ordered intervals with explicit missing coverage.

    Raises:
        ValueError: Saved bounds or measurement counts are invalid.
    """
    invalid = (
        ~pl.col("measured").is_between(0, pl.col("pages"))
        | pl.any_horizontal(
            (~pl.col(*_QUANTILES).is_finite()).fill_null(value=False),
        )
        | pl.any_horizontal(pl.col(*_QUANTILES) < 0)
        | (
            (pl.col("measured") > 0)
            & pl.any_horizontal(pl.col(*_QUANTILES).is_null())
        )
        | (
            (pl.col("measured") == 0)
            & pl.any_horizontal(pl.col(*_QUANTILES).is_not_null())
        )
        | (pl.col("central_95_low") > pl.col("central_68_low"))
        | (pl.col("central_68_low") > pl.col("median_words"))
        | (pl.col("median_words") > pl.col("central_68_high"))
        | (pl.col("central_68_high") > pl.col("central_95_high"))
    )
    if frame.filter(invalid).height:
        message = "Invalid quality-analysis interval or measurement count."
        raise ValueError(message)


def load_articles(runtime: Path = Path()) -> pl.DataFrame:
    """Read current cached metrics for a background collector.

    Args:
        runtime: Runtime root containing immutable generations.

    Returns:
        Validated complete current cohort or typed first-use emptiness.
    """
    bundle = _read_bundle(runtime)
    if bundle is None:
        return pl.DataFrame(schema=ARTICLE_SCHEMA)
    _validate(dict(bundle.tables))
    return bundle.tables["articles"]


def _read_bundle(runtime: Path) -> ReportBundle | None:
    """Retry one selector change without acquiring a writer lock.

    Returns:
        One immutable complete generation, or first-use absence.

    """
    store = ReportStore(runtime / _ROOT)
    try:
        return store.read(_KEY, locked=True)
    except FileNotFoundError:
        # A writer can prune the generation after its selector was read.
        return store.read(_KEY, locked=True)


def checkpoint(
    runtime: Path,
    *,
    members: pl.DataFrame | None = None,
    updates: pl.DataFrame | None = None,
    observed_at: dt.datetime,
) -> pl.DataFrame:
    """Atomically retain complete membership or bounded measurements.

    Args:
        runtime: Runtime owner of the separate analysis bundle.
        members: Full current quality cohort, or none to retain it.
        updates: Attempted measurements bound to exact source revisions.
        observed_at: Actual aware UTC time of the cohort observation.

    Returns:
        Current complete article table after atomic selection.

    Raises:
        ValueError: A measurement belongs to another selected cohort.
    """
    if observed_at.utcoffset() != dt.timedelta(0):
        message = "Quality-analysis observations require aware UTC time."
        raise ValueError(message)
    store = ReportStore(runtime / _ROOT)
    with store.locked(_KEY):
        saved = _read_bundle(runtime)
        frame = (
            saved.tables["articles"]
            if saved is not None
            else pl.DataFrame(schema=ARTICLE_SCHEMA)
        )
        if saved is not None:
            _validate(dict(saved.tables))
        previous = _saved_clock(saved) if saved is not None else None
        if members is not None:
            _validate_articles(members)
            member_clock = members["observed_at"].max()
            if member_clock is not None and member_clock != observed_at:
                message = "Quality membership has another observation clock."
                raise ValueError(message)
            if previous is not None and observed_at < previous:
                return frame
            cached = frame.select(
                *MEASURE_KEY,
                *(
                    pl.col(name).alias(f"saved_{name}")
                    for name in MEASURE_FIELDS
                    if name != "source_bytes"
                ),
            )
            frame = (
                members
                .join(cached, on=MEASURE_KEY, how="left")
                .with_columns(
                    *(
                        pl.col(f"saved_{name}").alias(name)
                        for name in MEASURE_FIELDS
                        if name != "source_bytes"
                    ),
                )
                .select(list(ARTICLE_SCHEMA))
            )
        if updates is not None:
            if updates.join(
                frame.select(MEASURE_KEY),
                on=MEASURE_KEY,
                how="anti",
            ).height:
                message = "Quality measurement no longer matches its cohort."
                raise ValueError(message)
            frame = (
                frame
                .join(
                    updates.select(
                        *MEASURE_KEY,
                        *(
                            pl.col(name).alias(f"new_{name}")
                            for name in MEASURE_FIELDS
                        ),
                    ),
                    on=MEASURE_KEY,
                    how="left",
                    validate="1:1",
                )
                .with_columns(
                    *(
                        pl.coalesce(f"new_{name}", name).alias(name)
                        for name in MEASURE_FIELDS
                    ),
                )
                .select(list(ARTICLE_SCHEMA))
            )
        tables = _summaries(frame)
        _validate(tables)
        cohort_clock = frame["observed_at"].max() or (
            observed_at if members is not None else previous or observed_at
        )
        store.write(
            _KEY,
            ReportResult(
                family="quality-analysis",
                tables=tables,
                metadata={"observed_at": cohort_clock.isoformat()},
            ),
            "",
            run_id=uuid4().hex,
            started_at=dt.datetime.now(dt.UTC),
            target={"site": "zhwiki", "title": "Video games quality analysis"},
            locked=True,
        )
        return tables["articles"]


def quality_analysis_identity(runtime: Path = Path()) -> tuple:
    """Observe the selector identity without data reads or writes.

    Args:
        runtime: Toolforge runtime root containing the analysis bundle.

    Returns:
        Bounded selector file metadata, or a missing first-use identity.
    """
    path = ReportStore(runtime / _ROOT).data_path(_KEY) / "latest.json"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return (None,)
    return (
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _saved_clock(bundle: ReportBundle) -> dt.datetime:
    """Validate the manifest clock against member observations.

    Returns:
        Actual UTC clock shared by the manifest and current members.

    Raises:
        ValueError: A source clock is invalid or inconsistent.
    """
    observed = bundle.manifest["metadata"].get("observed_at")
    try:
        clock = dt.datetime.fromisoformat(observed)
    except ValueError, TypeError:
        message = "Invalid quality-analysis source observation clock."
        raise ValueError(message) from None
    expected = bundle.tables["articles"]["observed_at"].max()
    if clock.utcoffset() != dt.timedelta(0) or (
        expected is not None and clock != expected
    ):
        message = "Quality-analysis cohort clocks are inconsistent."
        raise ValueError(message)
    return clock


def read_quality_analysis(runtime: Path = Path()) -> dict:
    """Read precomputed quality analysis without source reads or writes.

    Args:
        runtime: Runtime root independent of the uploaded source tree.

    Returns:
        Saved article rows, chart summaries and coverage with ISO dates.
        A missing first-use bundle is explicitly unavailable. Legacy
        summaries remain unavailable until a background checkpoint
        rebuilds them from the retained article measurements.

    """
    bundle = _read_bundle(runtime)
    if bundle is None:
        return {
            "available": False,
            "observed_at": None,
            "articles": [],
            "grade_totals": [],
            "importance_totals": [],
            "importance_available": False,
            "year_totals": [],
            "years": [],
            "lengths": [],
            "lengths_available": False,
            "coverage": {
                "total": 0,
                "measured": 0,
                "dated": 0,
                "median_words": None,
            },
        }
    tables = dict(bundle.tables)
    _validate(tables)
    lengths_available = tables["lengths"].schema == pl.Schema(LENGTH_SCHEMA)
    importance_available = "importance_totals" in tables
    observed = _saved_clock(bundle).isoformat()
    articles = (
        tables["articles"]
        .select(
            "page_id",
            "title",
            "grade",
            "importance",
            "source_bytes",
            "prose_words",
            "prose_bytes",
            "article_revision_id",
            "talk_revision_id",
            "listed_oldid",
            pl.col("listed_date").dt.to_string("%Y-%m-%d"),
            pl.col("measured_at").dt.to_string("%+"),
        )
        .to_dicts()
    )
    years = [
        {
            "year": row["year"],
            "pages": row["pages"],
            "grades": {grade: row[grade] for grade in GRADES},
        }
        for row in tables["years"].to_dicts()
    ]
    return {
        "available": True,
        "observed_at": observed,
        "articles": articles,
        "years": years,
        "grade_totals": tables["grade_totals"].to_dicts(),
        "importance_totals": (
            tables["importance_totals"].to_dicts()
            if importance_available
            else []
        ),
        "importance_available": importance_available,
        "year_totals": tables["year_totals"].to_dicts(),
        "lengths": tables["lengths"].to_dicts() if lengths_available else [],
        "lengths_available": lengths_available,
        "coverage": tables["coverage"].row(0, named=True),
    }
