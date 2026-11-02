"""Verify assessment order and stable maintenance pagination."""

from __future__ import annotations

import unittest

from webservice.presentation import (
    assessment_rank,
    maintenance_items,
    maintenance_page,
)


class TestMaintenanceSorting(unittest.TestCase):
    """Sort assessments independently of labels and filter ranks."""

    @staticmethod
    def test_quality_hierarchy_and_unknowns() -> None:
        """Distinguish list grades and keep unknowns last both ways."""
        hierarchy = (
            "FA",
            "FL",
            "A",
            "AL",
            "GA",
            "Bplus",
            "B",
            "BL",
            "C",
            "CL",
            "D",
            "Start",
            "Stub",
            "List",
            "SL",
            "Substub",
            "Disambig",
            "Unassessed",
        )
        rows = [
            {
                "page_id": position,
                "title": f"Title {len(hierarchy) - position:02}",
                "assessment_class": grade,
            }
            for position, grade in enumerate(hierarchy)
        ]
        unknown = [
            {
                "page_id": 100,
                "title": "A unknown",
                "assessment_class": "custom",
            },
            {
                "page_id": 101,
                "title": "B unknown",
                "assessment_class": "Unknown",
            },
            {"page_id": 102, "title": "C missing", "assessment_class": None},
            {"page_id": 103, "title": "D absent"},
            {"page_id": 104, "title": "E empty", "assessment_class": " "},
        ]
        source = list(reversed(rows + unknown))
        descending = maintenance_items(source, "class", "desc")
        assert descending == rows + unknown
        assert (
            maintenance_items(source, "class", "asc")
            == list(reversed(rows)) + unknown
        )
        assert source == list(reversed(rows + unknown))
        assert maintenance_page(descending, "1")["items"] == rows[:10]
        assert (
            maintenance_page(descending, "2")["items"]
            == (rows + unknown)[10:20]
        )
        assert maintenance_items([], "class", "desc") == []

    @staticmethod
    def test_aliases_and_fixed_title_id_ties() -> None:
        """Recognize localized labels and keep deterministic ties."""
        rows = [
            {"page_id": 7, "title": "A", "assessment_class": " 典範 "},
            {"page_id": 3, "title": "B", "assessment_class": "Fa"},
            {"page_id": 2, "title": "B", "assessment_class": "典范"},
            {"page_id": 4, "title": "AA", "assessment_class": "特色列表"},
            {"page_id": 5, "title": "AAA", "assessment_class": "甲"},
            {"page_id": 6, "title": "AAAA", "assessment_class": "甲級列表"},
            {"page_id": 8, "title": "AAAAA", "assessment_class": "優良"},
            {"page_id": 9, "title": "AAAAAA", "assessment_class": "乙上"},
        ]
        assert [
            row["page_id"] for row in maintenance_items(rows, "class", "desc")
        ] == [
            7,
            2,
            3,
            4,
            5,
            6,
            8,
            9,
        ]
        assert [
            row["page_id"] for row in maintenance_items(rows, "class", "asc")
        ] == [
            9,
            8,
            6,
            5,
            4,
            7,
            2,
            3,
        ]
        assert assessment_rank("FA", "class") == assessment_rank("FL", "class")
        assert assessment_rank("A", "class") == assessment_rank("AL", "class")

    @staticmethod
    def test_importance_hierarchy() -> None:
        """Sort localized importance before unknown values."""
        grades = ("極高", "High", "中", "Low", "极低", "不適用")
        rows = [
            {
                "page_id": number,
                "title": str(-number),
                "assessment_importance": grade,
            }
            for number, grade in enumerate(grades)
        ]
        unknown = {"page_id": 100, "title": "Missing"}
        assert maintenance_items(
            [unknown, *reversed(rows)],
            "importance",
            "desc",
        ) == [*rows, unknown]
        assert maintenance_items([unknown, *rows], "importance", "asc") == [
            *reversed(rows),
            unknown,
        ]


if __name__ == "__main__":
    unittest.main()
