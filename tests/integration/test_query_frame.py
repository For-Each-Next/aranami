"""Test query collection across SQLAlchemy and Polars."""

from unittest import TestCase
from unittest.mock import patch

import polars as pl
from sqlalchemy import bindparam, create_engine, event, false, select
from sqlalchemy.exc import StatementError

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

    def test_empty_bytes_and_nulls_preserve_schema_and_close_connections(
        self,
    ) -> None:
        """Retain source values and close collection scopes."""
        engine = create_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(engine.dispose)
        executed, checked_in = [], []
        event.listen(
            engine,
            "before_cursor_execute",
            lambda *_: executed.append(True),
        )
        event.listen(engine, "checkin", lambda *_: checked_in.append(True))
        statement = select(
            bindparam("title").label("title"),
            bindparam("missing").label("missing"),
        )
        schema = {"title": pl.Binary, "missing": pl.Int64}
        replica = Replica("zhwiki")
        with patch(
            "shared.sources.quarry.query._create_engine",
            return_value=engine,
        ) as create:
            frame = replica.query(
                statement,
                parameters={"title": b"Video_game", "missing": None},
                schema_overrides=schema,
            ).collect()
            empty = replica.query(
                statement.where(false()),
                parameters={"title": b"Video_game", "missing": None},
                schema_overrides=schema,
            ).collect()
        assert frame.to_dicts() == [{"title": b"Video_game", "missing": None}]
        assert frame.schema == empty.schema == schema
        assert empty.is_empty()
        assert len(executed) == len(checked_in) == 2  # ruff: ignore[magic-value-comparison]
        create.assert_called_once()

    def test_failed_query_closes_connection(self) -> None:
        """Release the connection when statement execution fails."""
        engine = create_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(engine.dispose)
        checked_in = []
        event.listen(engine, "checkin", lambda *_: checked_in.append(True))
        frame = Replica("zhwiki").query(select(bindparam("required")))
        with (
            patch(
                "shared.sources.quarry.query._create_engine",
                return_value=engine,
            ),
            self.assertRaises(StatementError),  # ruff: ignore[pytest-unittest-raises-assertion]
        ):
            frame.collect()
        assert len(checked_in) == 1
