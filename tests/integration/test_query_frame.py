"""Test query collection across SQLAlchemy and Polars."""

from unittest import TestCase
from unittest.mock import patch

import polars as pl
from sqlalchemy import bindparam, create_engine, select

from shared.sources.quarry import QueryFrame, Replica


class TestQueryFrameIntegration(TestCase):
    """Test deferred query collection with local components."""

    def test_query_collects_bound_values_and_reuses_engine(self) -> None:
        """Collect bound values and reuse the replica connection."""
        engine = create_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(engine.dispose)
        statement = select(bindparam("value").label("value"))

        frame = Replica("zhwiki").query(
            statement,
            parameters={"value": 21},
            schema_overrides={"value": pl.Int64},
        )

        with patch(
            "shared.sources.quarry.query._create_engine",
            return_value=engine,
        ) as create_replica_engine:
            result = frame.collect()
            second_result = frame.collect()

        assert isinstance(frame, QueryFrame)
        assert isinstance(result, pl.DataFrame)
        assert result.to_dict(as_series=False) == {
            "value": [21],
        }
        assert result.schema == {"value": pl.Int64}
        assert result.equals(second_result)
        create_replica_engine.assert_called_once()
