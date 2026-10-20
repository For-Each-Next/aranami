"""Load the local maintenance taxonomy for background collection.

Validate the complete JSON file before a collector reads its rules. The
result preserves task order and translated labels and supplies explicit
false defaults for rule options. Invalid or unavailable files fail
instead of replacing the configured worklist with built-in rules.
"""

# Configuration type mismatches are invalid input values.
# ruff: file-ignore[type-check-without-type-error]

from __future__ import annotations

import copy
import json
import re
import stat
import unicodedata
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).with_name("maintenance.json")

_MAX_FILE_BYTES = 256 * 1024
_MAX_SECTIONS = 12
_MAX_TASKS = 64
_MAX_RULES = 16
_MAX_LABEL_CHARS = 120
_MAX_NAME_BYTES = 255
_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_LANGUAGES = frozenset({"en", "zh-hans", "zh-hant"})


def _object(
    value: object,
    *,
    required: set[str],
    optional: set[str] | None = None,
    location: str,
) -> dict[str, object]:
    """Require one object with exactly the supported fields.

    Returns:
        The checked object for further field validation.

    Raises:
        ValueError: The value is not an object with the required fields.
    """
    if not isinstance(value, dict):
        msg = f"{location} must be an object"
        raise ValueError(msg)
    allowed = required | (optional or set())
    if not required.issubset(value) or not value.keys() <= allowed:
        msg = (
            f"{location} requires {sorted(required)}; allows {sorted(allowed)}"
        )
        raise ValueError(msg)
    return value


def _list(value: object, *, maximum: int, location: str) -> list[object]:
    """Require a nonempty list within the configured resource limit.

    Returns:
        The checked list for element validation.

    Raises:
        ValueError: The value is not a nonempty list within the limit.
    """
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        msg = f"{location} must contain between 1 and {maximum} entries"
        raise ValueError(msg)
    return value


def _key(value: object, *, location: str) -> str:
    """Require a stable URL-safe section or task identifier.

    Returns:
        The valid identifier, unchanged.

    Raises:
        ValueError: The identifier does not match the supported format.
    """
    if not isinstance(value, str) or _KEY.fullmatch(value) is None:
        msg = f"{location} must match [a-z][a-z0-9_]{{0,63}}"
        raise ValueError(msg)
    return value


def _label_text(value: object, *, location: str) -> str:
    """Return a trimmed display label within its length limit.

    Raises:
        ValueError: The label is not a nonempty, bounded string.
    """
    if not isinstance(value, str):
        msg = f"{location} must be a string"
        raise ValueError(msg)
    result = value.strip()
    if not 1 <= len(result) <= _MAX_LABEL_CHARS:
        msg = f"{location} must contain 1 to {_MAX_LABEL_CHARS} characters"
        raise ValueError(msg)
    return result


def _label(value: object, *, location: str) -> str | dict[str, str]:
    """Preserve plain or translated display labels.

    Returns:
        A trimmed string or independent translation object.

    Raises:
        ValueError: The labels or translation keys are invalid.
    """
    if isinstance(value, str):
        return _label_text(value, location=location)
    if not isinstance(value, dict) or not value:
        msg = f"{location} must be a label string or translation object"
        raise ValueError(msg)
    if not value.keys() <= _LANGUAGES:
        msg = f"{location} supports only {sorted(_LANGUAGES)}"
        raise ValueError(msg)
    return {
        language: _label_text(text, location=f"{location}.{language}")
        for language, text in value.items()
    }


def _rule(value: object, *, location: str) -> dict[str, object]:
    """Validate one category or transcluded-template matching rule.

    Returns:
        An independent rule with explicit boolean defaults.

    Raises:
        ValueError: The rule has invalid fields, title, or options.
    """
    rule = _object(
        value,
        required={"kind", "name"},
        optional={"talk_page", "monthly"},
        location=location,
    )
    kind = rule["kind"]
    if not isinstance(kind, str) or kind not in {"category", "template"}:
        msg = f"{location}.kind must be category or template"
        raise ValueError(msg)
    name = rule["name"]
    if not isinstance(name, str):
        msg = f"{location}.name must be a string"
        raise ValueError(msg)
    if any(
        unicodedata.category(char).startswith("C")
        or unicodedata.category(char) in {"Zl", "Zp"}
        for char in name
    ):
        msg = f"{location}.name must not contain controls or line breaks"
        raise ValueError(msg)
    name = name.strip()
    if (
        not name
        or name.startswith(":")
        or "#" in name
        or len(name.encode("utf-8")) > _MAX_NAME_BYTES
    ):
        msg = f"{location}.name must be a title of at most 255 UTF-8 bytes"
        raise ValueError(msg)
    talk_page = rule.get("talk_page", False)
    monthly = rule.get("monthly", False)
    if not isinstance(talk_page, bool) or not isinstance(monthly, bool):
        msg = f"{location}.talk_page and monthly must be booleans"
        raise ValueError(msg)
    if monthly and kind != "category":
        msg = f"{location}.monthly is supported only for category rules"
        raise ValueError(msg)
    return {
        "kind": kind,
        "name": name,
        "talk_page": talk_page,
        "monthly": monthly,
    }


def validate_backlog_config(value: object) -> dict[str, object]:
    """Validate and independently normalize a maintenance taxonomy.

    Args:
        value: Decoded JSON with sections, tasks, and matching rules.

    Returns:
        A new configuration preserving order and translated labels, with
        explicit boolean defaults on each rule.

    Raises:
        ValueError: The structure, identifiers, or limits are invalid.
    """
    config = _object(value, required={"schema", "sections"}, location="config")
    if type(config["schema"]) is not int or config["schema"] != 1:
        msg = "config.schema must be integer 1"
        raise ValueError(msg)
    sections = _list(
        config["sections"],
        maximum=_MAX_SECTIONS,
        location="config.sections",
    )
    section_keys: set[str] = set()
    task_keys: set[str] = set()
    normalized_sections = []
    for section_index, section_value in enumerate(sections):
        location = f"config.sections[{section_index}]"
        section = _object(
            section_value,
            required={"key", "label", "tasks"},
            location=location,
        )
        section_key = _key(section["key"], location=f"{location}.key")
        if section_key in section_keys:
            msg = f"Duplicate section key: {section_key}"
            raise ValueError(msg)
        section_keys.add(section_key)
        tasks = _list(
            section["tasks"],
            maximum=_MAX_TASKS,
            location=f"{location}.tasks",
        )
        normalized_tasks = []
        for task_index, task_value in enumerate(tasks):
            task_location = f"{location}.tasks[{task_index}]"
            task = _object(
                task_value,
                required={"key", "label", "rules"},
                location=task_location,
            )
            task_key = _key(task["key"], location=f"{task_location}.key")
            if task_key in task_keys:
                msg = f"Duplicate task key: {task_key}"
                raise ValueError(msg)
            task_keys.add(task_key)
            if len(task_keys) > _MAX_TASKS:
                msg = f"Configuration exceeds {_MAX_TASKS} tasks"
                raise ValueError(msg)
            rules = [
                _rule(rule, location=f"{task_location}.rules[{rule_index}]")
                for rule_index, rule in enumerate(
                    _list(
                        task["rules"],
                        maximum=_MAX_RULES,
                        location=f"{task_location}.rules",
                    ),
                )
            ]
            if len({
                (
                    rule["kind"],
                    rule["name"],
                    rule["talk_page"],
                    rule["monthly"],
                )
                for rule in rules
            }) != len(rules):
                msg = f"{task_location} contains duplicate rules"
                raise ValueError(msg)
            normalized_tasks.append({
                "key": task_key,
                "label": _label(
                    task["label"],
                    location=f"{task_location}.label",
                ),
                "rules": rules,
            })
        normalized_sections.append({
            "key": section_key,
            "label": _label(section["label"], location=f"{location}.label"),
            "tasks": normalized_tasks,
        })
    return {"schema": 1, "sections": normalized_sections}


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject repeated JSON fields.

    Returns:
        An object with unique field names.

    Raises:
        ValueError: The source JSON object repeats a field name.
    """
    result = {}
    for key, value in pairs:
        if key in result:
            msg = f"Duplicate JSON field: {key}"
            raise ValueError(msg)
        result[key] = value
    return result


def load_backlog_config(
    path: Path | None = None,
) -> dict[str, object]:
    """Read and validate the bounded local maintenance JSON file.

    Args:
        path: Deployment-owned file; defaults to the adjacent taxonomy.

    Returns:
        An independent configuration with rule defaults filled.

    Raises:
        OSError: The file is absent or cannot be read.
        ValueError: The path is not a regular file, exceeds 256 KiB, or
            contains invalid UTF-8, JSON, or configuration fields.
    """  # ruff: ignore[docstring-extraneous-exception] - File calls propagate OSError.
    config_path = DEFAULT_CONFIG_PATH if path is None else path
    file_stat = config_path.lstat()
    if not stat.S_ISREG(file_stat.st_mode):
        msg = "Maintenance configuration must be a regular file"
        raise ValueError(msg)
    if file_stat.st_size > _MAX_FILE_BYTES:
        msg = "Maintenance configuration exceeds 256 KiB"
        raise ValueError(msg)
    with config_path.open("rb") as stream:
        content = stream.read(_MAX_FILE_BYTES + 1)
    if len(content) > _MAX_FILE_BYTES:
        msg = "Maintenance configuration exceeds 256 KiB"
        raise ValueError(msg)
    return validate_backlog_config(
        json.loads(content.decode("utf-8"), object_pairs_hook=_json_object),
    )


def backlog_tasks(config: dict[str, object]) -> list[dict[str, object]]:
    """Flatten validated tasks while retaining their section labels.

    Args:
        config: A taxonomy returned by the loader or validator.

    Returns:
        Independent task objects in configuration order, each with added
        section_key and section_label fields.
    """
    normalized = validate_backlog_config(config)
    return [
        {
            **copy.deepcopy(task),
            "section_key": section["key"],
            "section_label": copy.deepcopy(section["label"]),
        }
        for section in normalized["sections"]
        for task in section["tasks"]
    ]
