"""Column types that behave the same on Postgres 16 and on SQLite.

Postgres is the production target; SQLite exists so ``autobench run`` works with
zero infrastructure. Rather than writing dialect branches at every call site,
the differences are absorbed here:

* ``JSONBType``  -> ``JSONB`` on Postgres, ``JSON`` on SQLite
* ``StringArray``-> ``TEXT[]`` on Postgres, ``JSON`` list on SQLite
* ``Money`` / ``Reward`` -> ``NUMERIC`` everywhere, but returned as ``float``
  so callers never have to think about ``Decimal`` vs ``float`` arithmetic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Integer, Numeric, Text, TypeDecorator
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Dialect

JSONBType = JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql")


#: SQLite only auto-increments an ``INTEGER PRIMARY KEY``; a ``BIGINT`` one is
#: NOT NULL with no default and every insert fails. Postgres has no such quirk,
#: so the variant keeps 64-bit ids in production and working ids in dev.
AutoBigInt = BigInteger().with_variant(Integer, "sqlite")


class StringArray(TypeDecorator[list[str]]):
    """A list of short strings — tags, scopes, allowlisted domains."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.ARRAY(Text()))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: list[str] | None, dialect: Dialect) -> Any:
        return list(value) if value is not None else []

    def process_result_value(self, value: Any, dialect: Dialect) -> list[str]:
        if value is None:
            return []
        return list(value)


class _NumericAsFloat(TypeDecorator[float]):
    """NUMERIC in the database, ``float`` in Python.

    Rewards and costs are aggregated, averaged and bootstrapped constantly.
    Mixing ``Decimal`` and ``float`` in those paths raises ``TypeError`` at the
    least convenient moment, so the boundary is converted once, here.
    """

    impl = Numeric
    cache_ok = True

    def process_result_value(self, value: Any, dialect: Dialect) -> float | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return float(value)
        return float(value)


class Reward(_NumericAsFloat):
    """``reward ∈ [0, 1]`` with three decimals — enough for weighted rubrics.

    The ``precision``/``scale`` keywords are accepted (and ignored) so that
    Alembic autogenerate, which round-trips a type through ``repr()``, produces
    a migration that actually imports.
    """

    cache_ok = True

    def __init__(self, precision: int = 4, scale: int = 3) -> None:
        super().__init__(precision=precision, scale=scale)


class Money(_NumericAsFloat):
    """USD. Six decimals: a single cheap call can cost fractions of a cent."""

    cache_ok = True

    def __init__(self, precision: int = 12, scale: int = 6) -> None:
        super().__init__(precision=precision, scale=scale)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware on the way out, always.

    Postgres round-trips ``TIMESTAMPTZ`` correctly; SQLite has no timezone type
    and hands back a naive datetime. Subtracting one of those from an aware
    ``utcnow()`` raises ``TypeError: can't subtract offset-naive and
    offset-aware datetimes`` — at whichever call site happens to run first,
    which is a poor way to find out. Normalising here means no call site has to
    care which database it is talking to.
    """

    impl = DateTime
    cache_ok = True

    def __init__(self, timezone: bool = True) -> None:
        super().__init__(timezone=True)

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError("Expected a datetime from the database driver")
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
