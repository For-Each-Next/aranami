"""Verify maintenance taxonomy validation and local file loading."""

# The project uses unittest for source-failure assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from backend.website.backlog_config import (
    backlog_tasks,
    load_backlog_config,
    validate_backlog_config,
)


def _config() -> dict[str, object]:
    """Return a fresh minimal valid deployment configuration."""
    return {
        "schema": 1,
        "sections": [
            {
                "key": "sources",
                "label": {"en": "Sources", "zh-hans": "来源"},
                "tasks": [
                    {
                        "key": "unreferenced",
                        "label": "缺少来源",
                        "rules": [
                            {
                                "kind": "category",
                                "name": "Category:缺少来源的条目",
                            },
                        ],
                    },
                ],
            },
        ],
    }


class TestBacklogConfig(TestCase):
    """Require valid files and independent normalized results."""

    @staticmethod
    def test_deployment_taxonomy_retains_all_verified_tasks() -> None:
        """Load the complete checked-in maintenance taxonomy."""
        tasks = backlog_tasks(load_backlog_config())
        assert len(tasks) == 37  # ruff: ignore[magic-value-comparison]
        keys = {task["key"] for task in tasks}
        assert {
            "unreferenced",
            "expand_english",
            "expand_japanese",
            "proofread_translation",
        } <= keys

    @staticmethod
    def test_defaults_labels_and_flattening_remain_independent() -> None:
        """Preserve translations and order when normalizing defaults."""
        config = _config()
        original = copy.deepcopy(config)
        normalized = validate_backlog_config(config)
        assert config == original
        rule = normalized["sections"][0]["tasks"][0]["rules"][0]
        assert rule == {
            "kind": "category",
            "name": "Category:缺少来源的条目",
            "talk_page": False,
            "monthly": False,
        }
        tasks = backlog_tasks(normalized)
        assert tasks[0]["section_key"] == "sources"
        assert tasks[0]["section_label"] == {
            "en": "Sources",
            "zh-hans": "来源",
        }
        tasks[0]["rules"][0]["name"] = "Modified"
        tasks[0]["section_label"]["en"] = "Modified"
        assert normalized["sections"][0]["tasks"][0]["rules"][0] == rule
        assert normalized["sections"][0]["label"]["en"] == "Sources"
        rule["name"] = "Changed normalized result"
        assert config == original

    def test_schema_and_unknown_fields_are_rejected(self) -> None:
        """Reject unsupported versions and missing or unknown fields."""
        for schema in (True, False, 1.0, "1", 0, 2, None):
            with self.subTest(schema=schema):
                config = _config()
                config["schema"] = schema
                with self.assertRaises(ValueError):
                    validate_backlog_config(config)
        for level in ("config", "section", "task", "rule"):
            for operation in ("missing", "extra"):
                with self.subTest(level=level, operation=operation):
                    config = _config()
                    objects = {
                        "config": (config, "schema"),
                        "section": (config["sections"][0], "label"),
                        "task": (config["sections"][0]["tasks"][0], "label"),
                        "rule": (
                            config["sections"][0]["tasks"][0]["rules"][0],
                            "kind",
                        ),
                    }
                    target, required = objects[level]
                    if operation == "missing":
                        del target[required]
                    else:
                        target["unknown"] = True
                    with self.assertRaises(ValueError):
                        validate_backlog_config(config)

    def test_labels_and_keys_require_the_supported_contract(self) -> None:
        """Reject invalid identifiers and unsupported translations."""
        invalid_labels = (
            None,
            "",
            "  ",
            "x" * 121,
            {},
            {"zh": "来源"},
            {"en": "Sources", "fr": "Sources"},
            {"en": ""},
            {"zh-hant": 1},
        )
        for label in invalid_labels:
            for level in ("section", "task"):
                with self.subTest(label=label, level=level):
                    config = _config()
                    section = config["sections"][0]
                    target = (
                        section if level == "section" else section["tasks"][0]
                    )
                    target["label"] = label
                    with self.assertRaises(ValueError):
                        validate_backlog_config(config)
        for key in ("", "A", "1a", "with-dash", "a\n", "a" * 65, None):
            with self.subTest(key=key):
                config = _config()
                config["sections"][0]["key"] = key
                with self.assertRaises(ValueError):
                    validate_backlog_config(config)
        config = _config()
        config["sections"][0]["label"] = {"zh-hant": "  來源  "}
        result = validate_backlog_config(config)
        assert result["sections"][0]["label"] == {"zh-hant": "來源"}

    @staticmethod
    def test_template_rules_and_flattening_follow_configured_order() -> None:
        """Preserve flags and section order when flattening tasks."""
        config = _config()
        section = copy.deepcopy(config["sections"][0])
        section["key"] = "translation"
        task = section["tasks"][0]
        task["key"] = "proofread"
        task["rules"] = [
            {
                "kind": "template",
                "name": "Template:Proofread",
                "talk_page": True,
            },
        ]
        config["sections"].insert(0, section)
        tasks = backlog_tasks(config)
        assert [task["key"] for task in tasks] == ["proofread", "unreferenced"]
        assert [task["section_key"] for task in tasks] == [
            "translation",
            "sources",
        ]
        assert tasks[0]["rules"][0] == {
            "kind": "template",
            "name": "Template:Proofread",
            "talk_page": True,
            "monthly": False,
        }

    def test_duplicate_sections_tasks_and_rules(self) -> None:
        """Keep section identifiers and global task URLs unambiguous."""
        config = _config()
        config["sections"].append(copy.deepcopy(config["sections"][0]))
        with self.assertRaises(ValueError):
            validate_backlog_config(config)
        config["sections"][1]["key"] = "other"
        with self.assertRaises(ValueError):
            validate_backlog_config(config)
        config = _config()
        rules = config["sections"][0]["tasks"][0]["rules"]
        duplicate = {**rules[0], "talk_page": False, "monthly": False}
        rules.append(duplicate)
        with self.assertRaises(ValueError):
            validate_backlog_config(config)
        duplicate["talk_page"] = True
        assert validate_backlog_config(config)

    def test_rule_options_names_and_utf8_limits(self) -> None:
        """Accept namespaces and reject invalid rules."""
        invalid_changes = (
            {"kind": "page"},
            {"kind": None},
            {"kind": []},
            {"name": None},
            {"name": ""},
            {"name": " "},
            {"name": ":Category:Example"},
            {"name": "Example#section"},
            {"name": "Exam\nple"},
            {"name": "\nExample"},
            {"name": "Example\u2028"},
            {"name": "Exam\tple"},
            {"name": "Exam\x00ple"},
            {"name": "Exam\u200bple"},
            {"name": "x" * 256},
            {"name": "源" * 86},
            {"talk_page": 1},
            {"monthly": "false"},
            {"kind": "template", "monthly": True},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                config = _config()
                config["sections"][0]["tasks"][0]["rules"][0].update(changes)
                with self.assertRaises(ValueError):
                    validate_backlog_config(config)
        for name in (
            "源" * 85,
            "Template:Cleanup",
            "分類:缺少來源",
            "模板:清理",
        ):
            with self.subTest(name=name):
                config = _config()
                config["sections"][0]["tasks"][0]["rules"][0]["name"] = name
                assert validate_backlog_config(config)

    def test_nonempty_lists_and_resource_limits(self) -> None:
        """Bound section, task, and rule counts before collection."""
        for level in ("sections", "tasks", "rules"):
            for replacement in ([], None, {}, "list"):
                with self.subTest(level=level, replacement=replacement):
                    config = _config()
                    target = config
                    if level != "sections":
                        target = config["sections"][0]
                    if level == "rules":
                        target = target["tasks"][0]
                    target[level] = replacement
                    with self.assertRaises(ValueError):
                        validate_backlog_config(config)
        for section_count, task_count, rule_count in (
            (13, 1, 1),
            (1, 65, 1),
            (1, 1, 17),
            (2, 33, 1),
        ):
            with self.subTest(counts=(section_count, task_count, rule_count)):
                config = _config()
                section = config["sections"][0]
                task = section["tasks"][0]
                rule = task["rules"][0]
                config["sections"] = [
                    {
                        **section,
                        "key": f"section_{section_index}",
                        "tasks": [
                            {
                                **task,
                                "key": f"task_{section_index}_{task_index}",
                                "rules": [
                                    {
                                        **rule,
                                        "name": f"Category:Rule {rule_index}",
                                    }
                                    for rule_index in range(rule_count)
                                ],
                            }
                            for task_index in range(task_count)
                        ],
                    }
                    for section_index in range(section_count)
                ]
                with self.assertRaises(ValueError):
                    validate_backlog_config(config)

    def test_loads_selected_or_default_file_without_fallback(self) -> None:
        """Fail when the selected file is absent or invalid."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "maintenance.json"
            path.write_text(json.dumps(_config()), encoding="utf-8")
            expected = validate_backlog_config(_config())
            assert load_backlog_config(path) == expected
            with patch(
                "backend.website.backlog_config.DEFAULT_CONFIG_PATH",
                path,
            ):
                assert load_backlog_config() == expected
            for content in (
                b"{invalid",
                b'{"schema":1,"schema":1,"sections":[]}',
                b"\xff",
                b"{}",
                b" " * (256 * 1024 + 1),
            ):
                with self.subTest(content=content[:30]):
                    path.write_bytes(content)
                    with self.assertRaises(ValueError):
                        load_backlog_config(path)
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                load_backlog_config(path)

    def test_regular_files_only(self) -> None:
        """Require regular files and reject symlinks."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                load_backlog_config(root)
            target = root / "target.json"
            target.write_text(json.dumps(_config()), encoding="utf-8")
            link = root / "maintenance.json"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                load_backlog_config(link)
