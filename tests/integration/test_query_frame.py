"""Test query collection across SQLAlchemy and Polars."""

from unittest import TestCase
from unittest.mock import patch

import polars as pl
from sqlalchemy import bindparam, create_engine, select

from aranami.sources.quarry import QueryFrame, Replica


class TestQueryFrameIntegration(TestCase):
    """Test deferred query collection with local components."""

    def test_query_collects_bound_values_and_postprocessors(self) -> None:
        """Collect a bound select and its lazy Polars pipeline."""
        engine = create_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(engine.dispose)
        statement = select(bindparam("value").label("value"))
        frame = (
            Replica("zhwiki")
            .query(
                statement,
                parameters={"value": 21},
                schema_overrides={"value": pl.Int64},
            )
            .pipe(
                lambda lazy_frame: lazy_frame.with_columns(
                    (pl.col("value") * 2).alias("doubled"),
                ),
            )
        )

        with patch(
            "aranami.sources.quarry.query._create_engine",
            return_value=engine,
        ) as create_replica_engine:
            result = frame.collect()
            second_result = frame.collect()

        assert isinstance(frame, QueryFrame)
        assert result.to_dict(as_series=False) == {
            "value": [21],
            "doubled": [42],
        }
        assert result.equals(second_result)
        create_replica_engine.assert_called_once()
