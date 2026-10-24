"""Find primary articles displayed on Chinese Wikipedia's homepage.

The collectors read the actual display sources and return primary
article subjects, excluding incidental links in their descriptions.
Dates identify the UTC display day rather than a nomination, news event,
historical anniversary year, or unrelated article edit date. Daily
archives identify featured, good, and anniversary displays; complete
template histories identify DYK and news appearance intervals.
Future featured and good schedules use current daily sources and are
labelled scheduled rather than reported as actual appearances.

Display contracts were checked on 18 October 2026 against
Template:Feature (61219352), Template:Good (71637889),
Template:Dyk (95136566), Template:Dyk/auto (78638565),
Template:Itn (95093293), Template:Otd
(26632828), and Template:Otd/format (71864171). Each number identifies
the source at https://zh.wikipedia.org/w/index.php?oldid=REVISION.
The July 21 history boundary was checked against Dyk (93569126),
Itn (93556728), Itn/Ongoing (93065907), and Itn/Recentdeaths (93567427).
"""

# Public APIs document ValueErrors propagated by their source parsers.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING
from urllib.parse import quote

import mwparserfromhell
import polars as pl
from pywikibot.exceptions import NoPageError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from mwparserfromhell.nodes import Template
    from mwparserfromhell.wikicode import Wikicode

    type HistoryReader = Callable[
        [str, dt.date, dt.date],
        list[dict[str, str | dt.datetime]],
    ]
    type SnapshotReader = Callable[[str, dt.datetime], str]
    type BatchResolver = Callable[[Iterable[str]], None]

_FEATURED_ROOT = "Wikipedia:典范条目/"
_GOOD_ROOT = "Wikipedia:优良条目/"
_HISTORY_ROOT = "Wikipedia:历史上的今天/"
_FEATURED_TYPES = {
    "Wikipedia:典范条目/": "fa",
    "Wikipedia:特色列表/": "fl",
}


def _date_label(day: dt.date) -> str:
    return f"{day.year}年{day.month}月{day.day}日"


def _days(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    """Yield display days used to select independently archived sources.

    Args:
        start: Inclusive UTC display day.
        end: Exclusive UTC display day.

    Yields:
        Each UTC date in the half-open interval.

    Raises:
        ValueError: The interval is empty or reversed.
    """
    if end <= start:
        message = "Homepage collection requires a nonempty UTC date interval."
        raise ValueError(message)
    for offset in range((end - start).days):
        yield start + dt.timedelta(days=offset)


def _title(value: str) -> str:
    return value.strip().lstrip(":").partition("#")[0].replace("_", " ")


def _bold_titles(code: Wikicode) -> list[str]:
    """Extract subjects bolded around a link or within its label.

    Args:
        code: Parsed display text.

    Returns:
        Linked titles marked as primary subjects by bold text.
    """
    titles = [
        str(link.title)
        for tag in code.filter_tags()
        if str(tag.tag).casefold() in {"b", "strong"}
        for link in tag.contents.filter_wikilinks()
    ]
    titles.extend(
        str(link.title)
        for link in code.filter_wikilinks()
        if link.text is not None
        and any(
            str(tag.tag).casefold() in {"b", "strong"}
            for tag in link.text.filter_tags()
        )
    )
    return titles


def _items(
    titles: Iterable[str],
    day: dt.date,
    resolve: Callable[[str], str] | None,
    *,
    kind: str | None = None,
) -> list[dict[str, str]]:
    """Resolve primary subjects and construct presentation records.

    Args:
        titles: Primary article subjects extracted from display sources.
        day: UTC display date.
        resolve: Optional canonical-title and redirect resolver.
        kind: Optional featured-content class.

    Returns:
        Subjects with presentation URLs and display dates.
    """
    result = []
    for value in titles:
        candidate = _title(value)
        resolved = _title(resolve(candidate) if resolve else candidate)
        item = {
            "title": resolved,
            "url": "https://zh.wikipedia.org/wiki/"
            + quote(resolved.replace(" ", "_"), safe="/:"),
            "date": day.isoformat(),
        }
        if kind:
            item["kind"] = kind
        result.append(item)
    return result


def _prime_subjects(
    titles: Iterable[str],
    resolve_many: BatchResolver | None,
) -> None:
    """Prime unique subjects before per-title lookups.

    Args:
        titles: Primary subjects from the sources in this collection.
        resolve_many: Optional batch primer for the canonical resolver.
    """
    if resolve_many is not None:
        candidates = list(dict.fromkeys(_title(title) for title in titles))
        if candidates:
            resolve_many(candidates)


def _finish(
    records: list[dict[str, str]],
    members: set[str] | None,
    *,
    separate_kinds: bool = False,
) -> list[dict[str, str]]:
    """Filter and deduplicate article appearances with explicit columns.

    Args:
        records: Parsed appearance records.
        members: Allowed canonical titles, or none for the entire wiki.
        separate_kinds: Keep different content slots on the same date.

    Returns:
        Unique article/display-day records, newest dates first.
    """
    if not records:
        return []
    frame = pl.DataFrame(
        records,
        schema=dict.fromkeys(records[0], pl.String),
    ).unique(
        subset=["title", "date", *(["kind"] if separate_kinds else [])],
        keep="first",
    )
    if members is not None:
        frame = frame.filter(
            pl.col("title").is_in([_title(title) for title in members]),
        )
    return frame.sort(["date", "title"], descending=[True, False]).to_dicts()


def _day_text(
    title: str,
    day: dt.date,
    read: Callable[[str], str],
    read_at: SnapshotReader | None,
) -> str:
    """Select source text strictly before the display day's UTC end.

    Args:
        title: Daily source title.
        day: UTC display date.
        read: Current wikitext provider for fixtures without snapshots.
        read_at: Historical reader that also clamps future dates to now.

    Returns:
        Historical display text, or current text without a reader.
    """
    boundary = dt.datetime.combine(
        day + dt.timedelta(days=1),
        dt.time(),
        tzinfo=dt.UTC,
    )
    return read_at(title, boundary) if read_at else read(title)


def _display(
    root: str,
    day: dt.date,
    read: Callable[[str], str],
    read_at: SnapshotReader | None,
) -> Wikicode:
    """Read a daily display using the homepage's yesterday fallback.

    Args:
        root: Daily display page root ending with a slash.
        day: UTC display date.
        read: Read-only current wikitext provider.
        read_at: Optional snapshots before an exclusive UTC boundary.

    Returns:
        Parsed daily display, using yesterday only if today is missing.
    """
    try:
        text = _day_text(root + _date_label(day), day, read, read_at)
    except NoPageError:
        text = _day_text(
            root + _date_label(day - dt.timedelta(days=1)),
            day,
            read,
            read_at,
        )
    return mwparserfromhell.parse(text)


def _scheduled_subject(
    code: Wikicode,
    roots: dict[str, str],
) -> tuple[str, str]:
    """Read the article-introduction transclusion in a daily display.

    Args:
        code: Parsed daily display.
        roots: Verified introduction page roots and content classes.

    Returns:
        Primary article title and featured-content class.

    Raises:
        ValueError: The source has no unique recognised introduction.
    """
    subjects = [
        (name.removeprefix(root), kind)
        for template in code.filter_templates(recursive=False)
        if (name := str(template.name).strip())
        for root, kind in roots.items()
        if name.startswith(root)
    ]
    if len(subjects) != 1:
        message = "The daily homepage display has an unknown source format."
        raise ValueError(message)
    return subjects[0]


def collect_featured(  # ruff: ignore[too-many-arguments]
    start: dt.date,
    end: dt.date,
    members: set[str] | None,
    read: Callable[[str], str],
    *,
    resolve: Callable[[str], str] | None = None,
    resolve_many: BatchResolver | None = None,
    read_at: SnapshotReader | None = None,
) -> list[dict[str, str]]:
    """Find featured article and list displays during a UTC interval.

    Args:
        start: Inclusive UTC display date.
        end: Exclusive UTC display date.
        members: Allowed canonical titles, or none for the entire wiki.
        read: Read-only current wikitext provider.
        resolve: Optional canonical-title and redirect resolver.
        resolve_many: Optional batch primer for the canonical resolver.
        read_at: Optional source snapshots before each UTC day's end.

    Returns:
        Matching subject with display date and featured-content kind.

    Raises:
        ValueError: The interval or display contract is unsupported.
    """
    displays = []
    for day in _days(start, end):
        title, kind = _scheduled_subject(
            _display(_FEATURED_ROOT, day, read, read_at),
            _FEATURED_TYPES,
        )
        displays.append((day, title, kind))
    _prime_subjects((title for _day, title, _kind in displays), resolve_many)
    records = [
        item
        for day, title, kind in displays
        for item in _items([title], day, resolve, kind=kind)
    ]
    return _finish(records, members)


def collect_good(  # ruff: ignore[too-many-arguments]
    start: dt.date,
    end: dt.date,
    members: set[str] | None,
    read: Callable[[str], str],
    *,
    resolve: Callable[[str], str] | None = None,
    resolve_many: BatchResolver | None = None,
    read_at: SnapshotReader | None = None,
) -> list[dict[str, str]]:
    """Find good-article displays during a UTC interval.

    Args:
        start: Inclusive UTC display date.
        end: Exclusive UTC display date.
        members: Allowed canonical titles, or none for the entire wiki.
        read: Read-only current wikitext provider.
        resolve: Optional canonical-title and redirect resolver.
        resolve_many: Optional batch primer for the canonical resolver.
        read_at: Optional source snapshots before each UTC day's end.

    Returns:
        Matching subject with its actual display date.

    Raises:
        ValueError: The interval or display contract is unsupported.
    """
    displays = []
    for day in _days(start, end):
        title, kind = _scheduled_subject(
            _display(_GOOD_ROOT, day, read, read_at),
            {_GOOD_ROOT: "ga"},
        )
        displays.append((day, title, kind))
    _prime_subjects((title for _day, title, _kind in displays), resolve_many)
    records = [
        item
        for day, title, kind in displays
        for item in _items([title], day, resolve, kind=kind)
    ]
    return _finish(records, members)


def collect_upcoming(  # ruff: ignore[too-many-arguments]
    start: dt.date,
    end: dt.date,
    members: set[str] | None,
    read: Callable[[str], str],
    *,
    resolve: Callable[[str], str] | None = None,
    resolve_many: BatchResolver | None = None,
) -> list[dict[str, str]]:
    """Find scheduled featured and good slots in current sources.

    Missing daily pages mean that no explicit slot is scheduled. Unlike
    actual homepage displays, future dates never reuse yesterday's slot.
    Schedules may change before their date and do not prove appearances.

    Args:
        start: Inclusive scheduled UTC date.
        end: Exclusive scheduled UTC date.
        members: Allowed canonical titles, or none for the entire wiki.
        read: Read-only current wikitext provider.
        resolve: Optional canonical-title and redirect resolver.
        resolve_many: Optional batch primer for the canonical resolver.

    Returns:
        Scheduled article slots with dates and FA, FL, or GA kinds.

    Raises:
        ValueError: The interval or an existing source is unsupported.
    """
    displays = []
    for root, kinds in (
        (_FEATURED_ROOT, _FEATURED_TYPES),
        (_GOOD_ROOT, {_GOOD_ROOT: "ga"}),
    ):
        for day in _days(start, end):
            try:
                text = read(root + _date_label(day))
            except NoPageError:
                continue
            title, kind = _scheduled_subject(
                mwparserfromhell.parse(text),
                kinds,
            )
            displays.append((day, title, kind))
    _prime_subjects((title for _day, title, _kind in displays), resolve_many)
    records = [
        {**item, "date_kind": "scheduled"}
        for day, title, kind in displays
        for item in _items([title], day, resolve, kind=kind)
    ]
    return _finish(records, members, separate_kinds=True)


def _template(code: Wikicode, name: str) -> Template:
    """Require a single verified display template.

    Args:
        code: Parsed display source.
        name: Verified template name.

    Returns:
        The unique matching template.

    Raises:
        ValueError: The source has no unique matching display template.
    """
    templates = [
        template
        for template in code.filter_templates()
        if str(template.name).strip().casefold() == name.casefold()
    ]
    if len(templates) != 1:
        message = f"Expected one {name} homepage display template."
        raise ValueError(message)
    return templates[0]


def _dyk_subjects(text: str) -> list[str]:
    """Read primary subjects from the six displayed DYK questions.

    Args:
        text: DYK display template source at one revision.

    Returns:
        Primary article titles from numeric question parameters.
    """
    template = _template(mwparserfromhell.parse(text), "Dyk/auto")
    return [
        title
        for index in range(6)
        if template.has(str(index))
        for title in _bold_titles(template.get(str(index)).value)
    ]


def _bulletin_intervals(  # ruff: ignore[too-many-arguments]
    title: str,
    start: dt.date,
    end: dt.date,
    read: Callable[[str], str],
    parser: Callable[[str], list[str]],
    *,
    resolve: Callable[[str], str] | None,
    resolve_many: BatchResolver | None,
    history: HistoryReader | None,
) -> pl.DataFrame:
    """Turn complete bulletin revisions into bounded display intervals.

    Args:
        title: Live display template title.
        start: Inclusive UTC display date.
        end: Exclusive UTC display date.
        read: Read-only current wikitext provider.
        parser: Primary-subject parser for this display template.
        resolve: Optional canonical-title and redirect resolver.
        resolve_many: Optional batch primer for the canonical resolver.
        history: Complete revisions, including the last before start.

    Returns:
        Subject intervals with inclusive starts and exclusive ends.

    Raises:
        ValueError: Historical coverage or revision metadata is absent.
    """
    start_time = dt.datetime.combine(start, dt.time(), tzinfo=dt.UTC)
    if end <= start:
        message = "Homepage collection requires a nonempty UTC date interval."
        raise ValueError(message)
    if history is None:
        if end != start + dt.timedelta(days=1):
            message = (
                "Complete revision history is required for past displays."
            )
            raise ValueError(message)
        revisions = [{"text": read(title), "timestamp": start_time}]
    else:
        revisions = history(title, start, end)
    if not revisions:
        message = f"No readable display history for {title}."
        raise ValueError(message)
    states = []
    parsed = []
    for index, revision in enumerate(revisions):
        timestamp = revision.get("timestamp")
        text = revision.get("text")
        if (
            not isinstance(timestamp, dt.datetime)
            or timestamp.tzinfo is None
            or not isinstance(text, str)
        ):
            message = "Display history needs source text and UTC timestamps."
            raise ValueError(message)
        states.append({"_revision": index, "_timestamp": timestamp})
        parsed.append((index, parser(text)))
    _prime_subjects(
        (subject for _index, titles in parsed for subject in titles),
        resolve_many,
    )
    subjects = [
        {"_revision": index, **item}
        for index, titles in parsed
        for item in _items(titles, start, resolve)
    ]
    schema = {
        "title": pl.String,
        "url": pl.String,
        "_begin": pl.Datetime(time_zone="UTC"),
        "_until": pl.Datetime(time_zone="UTC"),
        "date_kind": pl.String,
    }
    if not subjects:
        return pl.DataFrame(schema=schema)
    start_bound = pl.datetime(
        start.year,
        start.month,
        start.day,
        time_zone="UTC",
    )
    end_bound = pl.datetime(end.year, end.month, end.day, time_zone="UTC")
    timeline = (
        pl
        .DataFrame(
            states,
            schema={
                "_revision": pl.Int64,
                "_timestamp": pl.Datetime(time_zone="UTC"),
            },
        )
        .sort("_timestamp", maintain_order=True)
        .with_columns(
            pl
            .col("_timestamp")
            .shift(-1)
            .fill_null(end_bound)
            .alias("_until"),
            pl
            .when(pl.col("_timestamp") < start_bound)
            .then(start_bound)
            .otherwise(pl.col("_timestamp"))
            .alias("_begin"),
            pl
            .when(pl.col("_timestamp") < start_bound)
            .then(pl.lit("window_start"))
            .otherwise(pl.lit("appearance" if history else "observed"))
            .alias("date_kind"),
        )
    )
    return (
        pl
        .DataFrame(
            subjects,
            schema={
                "_revision": pl.Int64,
                "title": pl.String,
                "url": pl.String,
                "date": pl.String,
            },
        )
        .join(
            timeline,
            on="_revision",
            how="inner",
        )
        .filter(
            (pl.col("_until") > start_bound)
            & (pl.col("_timestamp") < end_bound)
            & (pl.col("_until") > pl.col("_timestamp")),
        )
        .select(*schema)
    )


def _appearance_starts(
    intervals: list[pl.DataFrame],
    members: set[str] | None,
) -> list[dict[str, str]]:
    """Merge touching intervals to identify appearance dates.

    Args:
        intervals: Subject intervals from homepage display templates.
        members: Allowed canonical titles, or none for the entire wiki.

    Returns:
        Appearance starts, with window-start dates labelled explicitly.
    """
    starts = (
        pl
        .concat(intervals)
        .sort(
            ["title", "_begin", "date_kind", "_until"],
            descending=[False, False, True, False],
        )
        .with_columns(
            pl
            .col("_until")
            .cum_max()
            .shift(1)
            .over("title")
            .alias("_previous_end"),
        )
        .filter(
            pl.col("_previous_end").is_null()
            | (pl.col("_begin") > pl.col("_previous_end")),
        )
        .select(
            "title",
            "url",
            "date_kind",
            pl.col("_begin").dt.strftime("%Y-%m-%d").alias("date"),
        )
    )
    return _finish(starts.to_dicts(), members)


def collect_dyk(  # ruff: ignore[too-many-arguments]
    start: dt.date,
    end: dt.date,
    members: set[str] | None,
    read: Callable[[str], str],
    *,
    resolve: Callable[[str], str] | None = None,
    resolve_many: BatchResolver | None = None,
    history: HistoryReader | None = None,
) -> list[dict[str, str]]:
    """Find DYK subjects from actual homepage display revisions.

    Args:
        start: Inclusive UTC display date.
        end: Exclusive UTC display date.
        members: Allowed canonical titles, or none for the entire wiki.
        read: Read-only current wikitext provider.
        resolve: Optional canonical-title and redirect resolver.
        resolve_many: Optional batch primer for the canonical resolver.
        history: Complete revisions, including the last before start.

    Returns:
        Primary subjects dated by appearance, not DYK promotion.
        Carried-in subjects have date_kind=window_start.

    Raises:
        ValueError: The interval or display contract is unsupported.
    """
    intervals = _bulletin_intervals(
        "Template:Dyk",
        start,
        end,
        read,
        _dyk_subjects,
        resolve=resolve,
        resolve_many=resolve_many,
        history=history,
    )
    return _appearance_starts([intervals], members)


def _column(code: Wikicode, identifier: str) -> Wikicode:
    """Require the bulletin content column, excluding its footer.

    Args:
        code: Parsed live bulletin.
        identifier: Expected content column identifier.

    Returns:
        Contents of the matching display column.

    Raises:
        ValueError: The source has no unique matching content column.
    """
    columns = [
        tag.contents
        for tag in code.filter_tags()
        if str(tag.tag).casefold() == "div"
        and any(
            str(attribute.name).strip() == "id"
            and str(attribute.value).strip() == identifier
            for attribute in tag.attributes
        )
    ]
    if len(columns) != 1:
        message = f"Expected one {identifier} homepage content column."
        raise ValueError(message)
    return columns[0]


def _separated_subjects(text: str) -> list[str]:
    """Read primary subjects in ongoing-news or recent-death entries.

    Args:
        text: Current auxiliary display template source.

    Returns:
        Primary article link from each displayed entry.
    """
    template = _template(mwparserfromhell.parse(text), "Separated entries")
    return [
        str(links[0].title)
        for parameter in template.params
        if str(parameter.name).strip().isdecimal()
        and (links := parameter.value.filter_wikilinks())
    ]


def _headline_subjects(text: str) -> list[str]:
    """Read primary headline subjects while excluding bulletin footers.

    Args:
        text: ITN headline display source at one revision.

    Returns:
        Bold primary article titles within the headline content column.
    """
    return _bold_titles(_column(mwparserfromhell.parse(text), "column-itn"))


def collect_news(  # ruff: ignore[too-many-arguments]
    start: dt.date,
    end: dt.date,
    members: set[str] | None,
    read: Callable[[str], str],
    *,
    resolve: Callable[[str], str] | None = None,
    resolve_many: BatchResolver | None = None,
    history: HistoryReader | None = None,
) -> list[dict[str, str]]:
    """Find news and death subjects from actual display revisions.

    Args:
        start: Inclusive UTC display date.
        end: Exclusive UTC display date.
        members: Allowed canonical titles, or none for the entire wiki.
        read: Read-only current wikitext provider.
        resolve: Optional canonical-title and redirect resolver.
        resolve_many: Optional batch primer for the canonical resolver.
        history: Complete revisions, including the last before start.

    Returns:
        Primary subjects dated by appearance, not news event dates.
        Continuous displays across headlines, ongoing news, and deaths
        count once. Carried-in subjects have date_kind=window_start.

    Raises:
        ValueError: The interval or display contract is unsupported.
    """
    intervals = [
        _bulletin_intervals(
            title,
            start,
            end,
            read,
            parser,
            resolve=resolve,
            resolve_many=resolve_many,
            history=history,
        )
        for title, parser in (
            ("Template:Itn", _headline_subjects),
            ("Template:Itn/Ongoing", _separated_subjects),
            ("Template:Itn/Recentdeaths", _separated_subjects),
        )
    ]
    return _appearance_starts(intervals, members)


def collect_history(  # ruff: ignore[too-many-arguments]
    start: dt.date,
    end: dt.date,
    members: set[str] | None,
    read: Callable[[str], str],
    *,
    resolve: Callable[[str], str] | None = None,
    resolve_many: BatchResolver | None = None,
    read_at: SnapshotReader | None = None,
) -> list[dict[str, str]]:
    """Find historical-anniversary subjects on selected calendar dates.

    Args:
        start: Inclusive UTC display date.
        end: Exclusive UTC display date.
        members: Allowed canonical titles, or none for the entire wiki.
        read: Read-only current wikitext provider.
        resolve: Optional canonical-title and redirect resolver.
        resolve_many: Optional batch primer for the canonical resolver.
        read_at: Optional source snapshots before each UTC day's end.

    Returns:
        Primary anniversary subjects dated by their homepage display.

    Raises:
        ValueError: The interval or display contract is unsupported.
    """
    displays = []
    for day in _days(start, end):
        label = f"{day.month}月{day.day}日"
        template = _template(
            mwparserfromhell.parse(
                _day_text(_HISTORY_ROOT + label, day, read, read_at),
            ),
            "Otd/format",
        )
        if (
            not template.has("date")
            or str(template.get("date").value).strip() != label
        ):
            message = "The anniversary display has a conflicting date."
            raise ValueError(message)
        titles = [
            title
            for index in range(1, 6)
            if template.has(f"event{index}")
            for title in _bold_titles(template.get(f"event{index}").value)
        ]
        displays.append((day, titles))
    _prime_subjects(
        (title for _day, titles in displays for title in titles),
        resolve_many,
    )
    records = [
        item
        for day, titles in displays
        for item in _items(titles, day, resolve)
    ]
    return _finish(records, members)
