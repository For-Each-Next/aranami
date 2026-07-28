"""Test declarative Wiki Replica table mappings."""

from unittest import TestCase

from sqlalchemy import inspect
from sqlalchemy.dialects import mysql

from aranami.sources.quarry.tables import (
    Base,
    BinaryDecoder,
    EnumDecoder,
    PageAssessments,
    StringDecoder,
    TextDecoder,
)


class TestReplicaTables(TestCase):
    """Test MediaWiki and extension mappings used by Quarry."""

    @staticmethod
    def test_all_schema_tables_have_an_orm_identity_key() -> None:
        """Map every core and extension table with an identity key."""
        expected_table_count = 76

        assert len(Base.metadata.tables) == expected_table_count
        assert len(Base.registry.mappers) == expected_table_count
        assert all(mapper.primary_key for mapper in Base.registry.mappers)
        assert {
            "page",
            "page_assessments",
            "page_assessments_projects",
            "wb_items_per_site",
            "wbt_item_terms",
        } <= Base.metadata.tables.keys()
        assert inspect(PageAssessments).primary_key == (
            PageAssessments.pa_page_id,
            PageAssessments.pa_project_id,
        )

    @staticmethod
    def test_binary_decoder_handles_chinese_text() -> None:
        """Encode query values and decode replica results as text."""
        decoder = BinaryDecoder()
        dialect = mysql.dialect()

        encoded = decoder.process_bind_param("电子游戏", dialect)

        assert encoded == "电子游戏".encode()
        assert decoder.process_result_value(encoded, dialect) == "电子游戏"
        assert decoder.process_result_value("电子游戏", dialect) == "电子游戏"
        assert decoder.process_bind_param(None, dialect) is None
        assert decoder.process_result_value(None, dialect) is None

    @staticmethod
    def test_character_decoders_handle_replica_bytes() -> None:
        """Return strings when a replica driver emits bytes."""
        dialect = mysql.dialect()
        values = (
            (StringDecoder(20), "典范"),
            (TextDecoder(), "特色列表"),
            (EnumDecoder("典范", "特色列表"), "典范"),
        )

        for decoder, value in values:
            assert decoder.process_bind_param(value, dialect) == value
            assert decoder.process_result_value(value, dialect) == value
            assert (
                decoder.process_result_value(value.encode(), dialect) == value
            )
            assert decoder.process_bind_param(None, dialect) is None
            assert decoder.process_result_value(None, dialect) is None

        assert isinstance(PageAssessments.pa_class.type, StringDecoder)
        assert isinstance(PageAssessments.pa_importance.type, StringDecoder)
        for column, value in (
            (PageAssessments.pa_class, "典范"),
            (PageAssessments.pa_importance, "高"),
        ):
            processor = column.type.result_processor(dialect, None)

            assert processor is not None
            assert processor(value.encode()) == value
