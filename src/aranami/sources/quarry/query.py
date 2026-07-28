"""Collect read-only Wikimedia Wiki Replica queries into Polars frames.

Use :class:`Replica` to bind a SQLAlchemy ``select()`` statement to a
Wiki Replica database. The resulting :class:`QueryFrame` defers the
query until ``collect()`` and supports lazy Polars postprocessing.

Examples:
    >>> from aranami.sources.quarry import QueryFrame, Replica
    >>> from aranami.sources.quarry.tables import Page
    >>> from sqlalchemy import select
    >>> frame = Replica("zhwiki").query(
    ...     select(Page.page_id, Page.page_title),
    ... )
    >>> isinstance(frame, QueryFrame)
    True
"""

from __future__ import annotations

__all__ = ("QueryFrame", "Replica")

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Self

import polars as pl
from polars import DataType
from polars.datatypes import DataTypeClass
from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.sql.selectable import CompoundSelect, Select

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

type FramePostprocessor = Callable[[pl.LazyFrame], pl.LazyFrame]
type PolarsDataType = DataTypeClass | DataType
type ReplicaStatement = Select[Any] | CompoundSelect[Any]

_CONNECTION_RECYCLE_SECONDS = 10 * 60
_CREDENTIALS_FILE: Final = Path(".my.cnf")


@dataclass(slots=True)
class Replica:
    """Describe one Wikimedia Wiki Replica database.

    Attributes:
        project: Project database name without the ``_p`` suffix.
        extension: Optional extension database. ``termstore`` is mainly
            used with ``wikidatawiki`` for Wikibase term data.

    Examples:
        >>> Replica("zhwiki").database
        'zhwiki_p'
        >>> Replica.wikidata_terms().database
        'wikidatawiki_p'

    """

    project: str
    extension: str | None = None
    _engine: Engine | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    @property
    def hostname(self) -> str:
        """Analytics service hostname for this replica."""
        target = (
            self.project
            if self.extension is None
            else f"{self.extension}.{self.project}"
        )
        return f"{target}.analytics.db.svc.wikimedia.cloud"

    @property
    def database(self) -> str:
        """Physical replica database name."""
        return f"{self.project}_p"

    @property
    def url(self) -> URL:
        """SQLAlchemy URL for this replica."""
        return URL.create(
            "mysql+pymysql",
            host=self.hostname,
            database=self.database,
            query={
                "charset": "utf8mb4",
                "read_default_file": str(_CREDENTIALS_FILE),
            },
        )

    def query(
        self,
        statement: ReplicaStatement,
        *,
        parameters: Mapping[str, Any] | None = None,
        schema_overrides: Mapping[str, PolarsDataType] | None = None,
    ) -> QueryFrame:
        """Create a deferred query frame.

        Args:
            statement: SQLAlchemy select expression to execute.
            parameters: Optional values for named bind parameters.
            schema_overrides: Optional Polars dtypes keyed by result
                column name.

        Returns:
            A frame that has not opened a database connection.

        """
        return QueryFrame(
            self,
            statement,
            parameters=parameters or {},
            schema_overrides=schema_overrides or {},
        )

    @property
    def engine(self) -> Engine:
        """Shared SQLAlchemy engine, created on first use."""
        if self._engine is not None:
            return self._engine
        self._engine = _create_engine(self)
        return self._engine

    @classmethod
    def from_site(
        cls,
        site: BaseSite,
        *,
        extension: str | None = None,
    ) -> Self:
        """Create replica configuration from a Pywikibot site.

        Args:
            site: Site whose database name identifies the project.
            extension: Optional extension database name.

        Returns:
            Replica configuration for the site's project.

        Examples:
            >>> from unittest.mock import Mock
            >>> site = Mock()
            >>> site.dbName.return_value = "zhwiki"
            >>> Replica.from_site(site).database
            'zhwiki_p'

        """
        return cls(site.dbName(), extension=extension)

    @classmethod
    def wikidata_terms(cls) -> Self:
        """Create configuration for Wikidata's termstore replica.

        Returns:
            Replica configuration for the Wikibase termstore database.

        """
        return cls(
            "wikidatawiki",
            extension="termstore",
        )


@dataclass(frozen=True, slots=True)
class QueryFrame:
    """Defer one replica select and its Polars postprocessors.

    Attributes:
        replica: Wiki Replica on which to execute the statement.
        statement: SQLAlchemy select expression to execute.
        parameters: Values for named bind parameters.
        schema_overrides: Polars dtypes keyed by result column name.

    Examples:
        >>> from sqlalchemy import literal, select
        >>> frame = Replica("zhwiki").query(
        ...     select(literal(1).label("value")),
        ... )
        >>> piped = frame.pipe(
        ...     lambda lazy_frame: lazy_frame.select("value"),
        ... )
        >>> piped is frame
        False

    """

    replica: Replica
    statement: ReplicaStatement
    parameters: Mapping[str, Any] = field(default_factory=dict)
    schema_overrides: Mapping[str, PolarsDataType] = field(
        default_factory=dict,
    )
    _postprocessors: tuple[FramePostprocessor, ...] = field(
        default=(),
        repr=False,
    )

    def pipe(self, postprocessor: FramePostprocessor) -> Self:
        """Append a lazy Polars postprocessor.

        Args:
            postprocessor: Function that accepts and returns a
                LazyFrame.

        Returns:
            A new frame containing the appended postprocessor.

        """
        return replace(
            self,
            _postprocessors=(*self._postprocessors, postprocessor),
        )

    def collect(self) -> pl.DataFrame:
        """Execute the query and collect its Polars postprocessors.

        Returns:
            The collected query result.

        """
        names, rows = _fetch(
            self.replica,
            self.statement,
            self.parameters,
        )
        lazy_frame = pl.LazyFrame(
            rows,
            schema=names,
            schema_overrides=dict(self.schema_overrides),
            orient="row",
        )
        for postprocessor in self._postprocessors:
            lazy_frame = postprocessor(lazy_frame)
        return lazy_frame.collect()


def _create_engine(replica: Replica) -> Engine:
    """Create a single-connection engine for a replica.

    Args:
        replica: Target database configuration.

    Returns:
        An engine that reuses one connection and recycles it every ten
        minutes.

    """
    return create_engine(
        replica.url,
        max_overflow=0,
        pool_recycle=_CONNECTION_RECYCLE_SECONDS,
        pool_size=1,
    )


def _fetch(
    replica: Replica,
    statement: ReplicaStatement,
    parameters: Mapping[str, Any],
) -> tuple[tuple[str, ...], list[tuple[Any, ...]]]:
    """Fetch a replica query while its connection is open.

    Args:
        replica: Target database configuration.
        statement: SQLAlchemy select expression to execute.
        parameters: Values for named bind parameters.

    Returns:
        Result-column names and materialized row tuples.

    """
    with replica.engine.connect() as connection:
        result = connection.execution_options(
            stream_results=True,
        ).execute(statement, dict(parameters))
        return tuple(result.keys()), [tuple(row) for row in result.fetchall()]
