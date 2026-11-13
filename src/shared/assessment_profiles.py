"""Resolve assessments from audited wiki and project metadata.

Select an exact project profile, then its site's general profile, then
lossless observed values. Raw storage declarations stay separate from
read-time aliases, display labels, and legacy presentation vocabulary.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import polars as pl

_DIMENSIONS = {"class", "importance"}
_TEXT_FIELDS = {
    "raw_value",
    "code",
    "long_name",
    "short_name",
    "article_group",
    "color",
}
_FIELDS = _TEXT_FIELDS | {"rank", "order", "is_article"}
_ROOT = Path(__file__).with_name("profiles") / "assessments"
_MAX_BYTES = 256 * 1024
_MAX_LABELS = 256


@dataclass(frozen=True, slots=True)
class AssessmentLabel:
    """Describe an observation and its presentation metadata.

    Attributes:
        raw_value: Exact source string, never rewritten in storage.
        code: Stable presentation code.
        long_name: Full display label.
        short_name: Compact display label.
        aliases: Audited read-time spellings, excluding unknown values.
        rank: Comparable grade rank, or none for unranked observations.
        order: Display order, with smaller values appearing first.
        color: Verified default display color, or none when unaudited.
        color_variants: Named presentation color overrides.
    """

    raw_value: str
    code: str
    long_name: str
    short_name: str
    aliases: tuple[str, ...] = field(default=(), kw_only=True)
    rank: int | None = field(default=None, kw_only=True)
    order: int | None = field(default=None, kw_only=True)
    color: str | None = field(default=None, kw_only=True)
    color_variants: tuple[tuple[str, str], ...] = field(
        default=(),
        kw_only=True,
    )


@dataclass(frozen=True, slots=True)
class ClassAssessmentLabel(AssessmentLabel):
    """Classify content independently of its page namespace.

    Attributes:
        is_article: Whether the class represents article/list content.
        article_group: Article grade used for list-class filtering.
    """

    is_article: bool = field(kw_only=True)
    article_group: str | None = field(default=None, kw_only=True)


@dataclass(frozen=True, slots=True)
class AssessmentProfile:
    """Manage one audited source vocabulary and its presentation rules.

    Attributes:
        identifier: Versioned identity of the declaration.
        classes: Raw class labels in project ranking order.
        importance: Raw importance labels in project ranking order.
        compatibility_classes: Display labels outside raw storage.
        compatibility_importance: Historical importance display labels.
        class_suffixes: Display suffixes accepted during lookup.
    """

    identifier: str
    classes: tuple[ClassAssessmentLabel, ...]
    importance: tuple[AssessmentLabel, ...]
    compatibility_classes: tuple[ClassAssessmentLabel, ...] = ()
    compatibility_importance: tuple[AssessmentLabel, ...] = ()
    class_suffixes: tuple[str, ...] = ()

    @property
    def class_values(self) -> tuple[str, ...]:
        """Exact raw class declarations in project order."""
        return tuple(label.raw_value for label in self.classes)

    @property
    def article_class_values(self) -> tuple[str, ...]:
        """Raw article/list classes in project order."""
        return tuple(
            label.raw_value for label in self.classes if label.is_article
        )

    @property
    def importance_values(self) -> tuple[str, ...]:
        """Exact raw importance declarations in project order."""
        return tuple(label.raw_value for label in self.importance)

    def class_label(
        self,
        raw_value: str | None,
    ) -> ClassAssessmentLabel | None:
        """Look up an exact raw class without alias or legacy coercion.

        Returns:
            Declared class metadata, or none for an unknown observation.
        """
        return next(
            (label for label in self.classes if label.raw_value == raw_value),
            None,
        )

    def importance_label(
        self,
        raw_value: str | None,
    ) -> AssessmentLabel | None:
        """Look up an exact raw importance without alias coercion.

        Returns:
            Declared importance metadata, or none for unknown values.
        """
        return next(
            (
                label
                for label in self.importance
                if label.raw_value == raw_value
            ),
            None,
        )

    def is_article_class(self, raw_value: str | None) -> bool | None:
        """Classify exact source grades independently of namespace.

        Returns:
            Declared article/nonarticle status, or none if unknown.
        """
        label = self.class_label(raw_value)
        return label.is_article if label is not None else None

    def labels(self, dimension: str) -> tuple[AssessmentLabel, ...]:
        """Select declared and compatibility labels for presentation.

        Args:
            dimension: Class or importance dimension.

        Returns:
            Labels in stable declaration order, including compatibility.

        Raises:
            ValueError: The dimension is unsupported.
        """
        if dimension not in _DIMENSIONS:
            message = "Assessment dimension must be class or importance."
            raise ValueError(message)
        return (
            self.classes + self.compatibility_classes
            if dimension == "class"
            else self.importance + self.compatibility_importance
        )

    def _key(self, value: str, dimension: str) -> str:
        """Normalize lookup text while preserving the observation.

        Returns:
            Lowercase comparison key with declared suffixes removed.
        """
        key = value.strip().lower()
        for suffix in self.class_suffixes if dimension == "class" else ():
            if key.endswith(suffix):
                return key.removesuffix(suffix)
        return key

    def aliases(self, dimension: str) -> dict[str, str]:
        """Map accepted lookup spellings to presentation codes.

        Returns:
            A fresh mapping that cannot mutate the registered profile.

        Raises:
            ValueError: Multiple grades share an ambiguous alias.
        """
        result = {}
        for label in self.labels(dimension):
            for value in (
                label.raw_value,
                label.code,
                label.long_name,
                label.short_name,
                *label.aliases,
            ):
                key = self._key(value, dimension)
                if key in result and result[key] != label.code:
                    message = "Conflicting assessment aliases."
                    raise ValueError(message)
                result[key] = label.code
        return result

    def resolve(self, value: object, dimension: str) -> AssessmentLabel | None:
        """Resolve declared labels and audited aliases without guessing.

        Args:
            value: Source observation or a presentation spelling.
            dimension: Class or importance dimension.

        Returns:
            Metadata, or none for null and unknown observations.
        """
        aliases = self.aliases(dimension)
        code = (
            aliases.get(self._key(value, dimension))
            if isinstance(value, str)
            else None
        )
        return next(
            (label for label in self.labels(dimension) if label.code == code),
            None,
        )

    def code(
        self,
        value: str | None,
        dimension: str,
        *,
        preserve_unknown: bool = True,
    ) -> str | None:
        """Select a known code while retaining unknown raw observations.

        Args:
            value: Unmodified source value.
            dimension: Class or importance dimension.
            preserve_unknown: Keep unmatched text; false yields null.

        Returns:
            Presentation code, unchanged unknown text, or null.
        """
        label = self.resolve(value, dimension)
        return label.code if label else value if preserve_unknown else None

    def expression(
        self,
        value: str | pl.Expr,
        dimension: str,
        *,
        field: str = "code",
        preserve_unknown: bool = True,
    ) -> pl.Expr:
        """Map assessments with native Polars expressions.

        Args:
            value: Source column name or expression.
            dimension: Class or importance dimension.
            field: Metadata field to project into a derived column.
            preserve_unknown: Keep unmatched text for text fields.

        Returns:
            Native expression; it never mutates the original column.

        Raises:
            ValueError: The field or unknown policy is invalid.
        """
        if field not in _FIELDS or (
            preserve_unknown and field not in _TEXT_FIELDS
        ):
            message = "Incompatible assessment field and unknown policy."
            raise ValueError(message)
        source = pl.col(value) if isinstance(value, str) else value
        key = source.cast(pl.String).str.strip_chars().str.to_lowercase()
        if dimension == "class" and self.class_suffixes:
            # Authored suffixes are validated literal characters.
            key = key.str.replace(
                "[" + "".join(self.class_suffixes) + "]$",
                "",
            )
        labels = {label.code: label for label in self.labels(dimension)}
        mapping = {
            alias: getattr(labels[code], field, None)
            for alias, code in self.aliases(dimension).items()
        }
        dtype = (
            pl.Boolean
            if field == "is_article"
            else pl.Int64
            if field in {"rank", "order"}
            else pl.String
        )
        return key.replace_strict(
            mapping,
            default=source if preserve_unknown else None,
            return_dtype=dtype,
        )

    def color(
        self,
        value: object,
        dimension: str,
        *,
        variant: str | None = None,
    ) -> str | None:
        """Select a verified color or named presentation override.

        Args:
            value: Source observation or supported presentation alias.
            dimension: Class or importance dimension.
            variant: Named display context, falling back to the default.

        Returns:
            Verified CSS color, or none for an unknown color.
        """
        label = self.resolve(value, dimension)
        if label is None:
            return None
        return dict(label.color_variants).get(variant, label.color)

    def codes(self, dimension: str) -> tuple[str, ...]:
        """Return every supported presentation code in stable order."""
        labels = sorted(
            self.labels(dimension),
            key=lambda label: (label.order is None, label.order or 0),
        )
        return tuple(label.code for label in labels)

    def ranks(self, dimension: str) -> dict[str, int]:
        """Return known comparable grade ranks."""
        return {
            label.code: label.rank
            for label in self.labels(dimension)
            if label.rank is not None
        }

    def sort_ranks(self, dimension: str) -> dict[str, int]:
        """Return distinct descending sort ranks."""
        ordered = {
            label.code: label.order
            for label in self.labels(dimension)
            if label.order is not None
        }
        maximum = max(ordered.values(), default=0)
        return {code: maximum - order for code, order in ordered.items()}


OBSERVED_PROFILE = AssessmentProfile("observed-raw-1", (), ())


def _colors(value: dict) -> dict:
    """Validate supported CSS colors without assigning unknown styles.

    Returns:
        Default color and immutable named overrides.

    Raises:
        ValueError: A color or variant identifier is invalid.
    """
    color, variants = value.get("color"), value.get("color_variants", {})
    if color is not None and (
        not isinstance(color, str)
        or not re.fullmatch(r"#[0-9A-Fa-f]{6}|transparent|white|red", color)
    ):
        message = "Invalid assessment color."
        raise ValueError(message)
    if not isinstance(variants, dict) or any(
        not isinstance(name, str)
        or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name)
        or not isinstance(shade, str)
        or not re.fullmatch(r"#[0-9A-Fa-f]{6}|transparent|white|red", shade)
        for name, shade in variants.items()
    ):
        message = "Invalid assessment color variant."
        raise ValueError(message)
    return {"color": color, "color_variants": tuple(sorted(variants.items()))}


def _label(value: dict, *, dimension: str) -> AssessmentLabel:
    """Validate one authored label without inferring missing semantics.

    Returns:
        Typed immutable label metadata.

    Raises:
        ValueError: Required metadata, aliases, or ranks are invalid.
    """
    required = {"raw_value", "code", "long_name", "short_name"}
    extra = {"aliases", "rank", "order", "color", "color_variants"}
    if dimension == "class":
        required.add("is_article")
        extra.add("article_group")
    if (
        not isinstance(value, dict)
        or not required <= value.keys()
        or not value.keys() <= required | extra
    ):
        message = "Invalid assessment label fields."
        raise ValueError(message)
    if (
        any(
            not isinstance(value[key], str)
            for key in ("raw_value", "code", "long_name", "short_name")
        )
        or not value["code"]
        or any(
            type(value.get(key)) is not int and value.get(key) is not None
            for key in ("rank", "order")
        )
        or not isinstance(value.get("aliases", []), list)
        or not all(
            isinstance(alias, str) for alias in value.get("aliases", [])
        )
    ):
        message = "Invalid assessment label values."
        raise ValueError(message)
    if dimension == "class" and (
        type(value["is_article"]) is not bool
        or (
            value.get("article_group") is not None
            and not isinstance(value["article_group"], str)
        )
    ):
        message = "Invalid article classification metadata."
        raise ValueError(message)
    constructor = (
        ClassAssessmentLabel if dimension == "class" else AssessmentLabel
    )
    return constructor(**{
        **value,
        **_colors(value),
        "aliases": tuple(value.get("aliases", [])),
    })


def _profile(entry: dict) -> AssessmentProfile:
    """Validate one authored profile and its lookup vocabulary.

    Returns:
        Immutable raw and presentation declarations.

    Raises:
        ValueError: Identity, labels, suffixes, or aliases are invalid.
    """
    if (
        not isinstance(entry, dict)
        or not isinstance(entry.get("identifier"), str)
        or not entry["identifier"]
        or "project" not in entry
    ):
        message = "Invalid assessment profile identity."
        raise ValueError(message)
    project = entry["project"]
    if project is not None and not isinstance(project, str):
        message = "Invalid assessment project identity."
        raise ValueError(message)
    kwargs = {}
    for name, dimension in (
        ("classes", "class"),
        ("importance", "importance"),
        ("compatibility_classes", "class"),
        ("compatibility_importance", "importance"),
    ):
        rows = entry.get(name, [])
        if not isinstance(rows, list) or len(rows) > _MAX_LABELS:
            message = "Invalid assessment label inventory."
            raise ValueError(message)
        kwargs[name] = tuple(_label(row, dimension=dimension) for row in rows)
    suffixes = entry.get("class_suffixes", [])
    if not isinstance(suffixes, list) or any(
        not isinstance(suffix, str) or len(suffix) != 1 or not suffix.isalpha()
        for suffix in suffixes
    ):
        message = "Invalid assessment class suffixes."
        raise ValueError(message)
    profile = AssessmentProfile(
        entry["identifier"],
        **kwargs,
        class_suffixes=tuple(suffixes),
    )
    for dimension in _DIMENSIONS:
        labels = profile.labels(dimension)
        if len({label.code for label in labels}) != len(labels):
            message = "Duplicate assessment codes."
            raise ValueError(message)
        profile.aliases(dimension)
    return profile


@lru_cache(maxsize=1)
def _registry() -> tuple[
    dict[tuple[str, str | None], AssessmentProfile],
    dict,
]:
    """Load bounded local policy records without runtime or wiki access.

    Returns:
        Exact source registry and the original audited configuration.

    Raises:
        ValueError: A declaration is malformed or has ambiguous aliases.
    """
    profiles, configuration = {}, {}
    for path in sorted(_ROOT.glob("*.json")):
        if path.stat().st_size > _MAX_BYTES:
            message = "Assessment metadata exceeds its source bound."
            raise ValueError(message)
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or data.get("schema") != 1
            or data.get("wiki") != path.stem
            or not isinstance(data.get("profiles"), list)
        ):
            message = "Invalid assessment profile document."
            raise ValueError(message)
        for entry in data["profiles"]:
            profile = _profile(entry)
            project = entry["project"]
            key = (data["wiki"], project)
            if key in profiles:
                message = "Duplicate assessment profile identity."
                raise ValueError(message)
            profiles[key], configuration[key] = profile, entry
    return profiles, configuration


def assessment_profile(site_name: str, project: str) -> AssessmentProfile:
    """Select a project override, site standard, or raw fallback.

    Args:
        site_name: Exact wiki database identity.
        project: Exact PageAssessments project title.

    Returns:
        Project policy, audited site policy, or lossless unknowns.
    """
    profiles, _ = _registry()
    return profiles.get(
        (site_name, project),
        profiles.get((site_name, None), OBSERVED_PROFILE),
    )


def assessment_profile_configuration() -> list[dict[str, object]]:
    """Expose audited raw declarations and display metadata.

    Returns:
        Identities, labels, aliases, ordering, and audit provenance.
    """
    profiles, configurations = _registry()
    return [
        {
            "wiki": wiki,
            "project": project,
            "profile_id": profile.identifier,
            "classes": [
                _configuration_label(label) for label in profile.classes
            ],
            "importance": [
                _configuration_label(label) for label in profile.importance
            ],
            "compatibility_classes": [
                _configuration_label(label)
                for label in profile.compatibility_classes
            ],
            "compatibility_importance": [
                _configuration_label(label)
                for label in profile.compatibility_importance
            ],
            "article_class_values": list(profile.article_class_values),
            "sources": deepcopy(
                configurations[wiki, project].get("sources", []),
            ),
        }
        for (wiki, project), profile in sorted(
            profiles.items(),
            key=lambda pair: (pair[0][0], pair[0][1] or ""),
        )
    ]


def _configuration_label(label: AssessmentLabel) -> dict:
    """Expose label fields with named color variants as a fresh mapping.

    Returns:
        JSON-compatible fields, independent of immutable metadata.
    """
    return {**asdict(label), "color_variants": dict(label.color_variants)}


ZHWIKI_VIDEO_GAMES = assessment_profile("zhwiki", "电子游戏")
