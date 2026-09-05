"""Compare the migrated database against the models that describe it.

The test suite builds its schema with `Base.metadata.create_all`, so it
sees whatever the models declare and never once reads a migration. The
production schema comes from `alembic/versions/`. Nothing had ever
compared the two, and on 2026-09-05 that cost two outages in one day:

  * three tables created NOT NULL with no `server_default=now()`, so every
    insert that did not spell its own timestamps failed — with a green
    test suite, because `create_all` had applied the default the mixin
    declares;
  * and before that, a seed row that passed a SQL function as a bind
    parameter, which only Postgres rejects.

This is deliberately narrower than `alembic check`. That compares
everything and currently reports a great deal of long-standing drift —
tables in the database whose models are not imported into the metadata,
indexes nobody declared — so gating on it would mean fixing all of that
first. What is checked here is the agreement that actually broke:

  * a column the model gives a server default must have one in the
    database, and
  * a column the model says is NOT NULL must be NOT NULL there.

Run against a database already at head. Exits non-zero and prints every
disagreement.
"""

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  — registers every model on Base.metadata
from app.models.base import Base


async def main() -> int:
    url = os.environ.get("PRINTOPS_DATABASE_URL")
    if not url:
        print("PRINTOPS_DATABASE_URL is not set", file=sys.stderr)
        return 2

    engine = create_async_engine(url)
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT table_name, column_name, column_default, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    """
                )
            )
        ).all()
    await engine.dispose()

    actual = {
        (table, column): (default, nullable == "YES") for table, column, default, nullable in rows
    }

    problems: list[str] = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            found = actual.get((table.name, column.name))
            if found is None:
                # A model whose table no migration creates is its own kind
                # of wrong, but it is not this script's question and it
                # would fire on anything mid-rename.
                continue
            db_default, db_nullable = found

            if column.server_default is not None and db_default is None:
                problems.append(
                    f"{table.name}.{column.name}: the model declares a server default "
                    f"and the database has none — every insert that omits it will fail"
                )
            if not column.nullable and db_nullable:
                problems.append(
                    f"{table.name}.{column.name}: the model says NOT NULL, "
                    f"the database allows nulls"
                )
            if column.nullable and not db_nullable:
                problems.append(
                    f"{table.name}.{column.name}: the model allows nulls, "
                    f"the database says NOT NULL"
                )

    if problems:
        print("The migrated schema disagrees with the models:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            f"\n{len(problems)} disagreement(s). "
            "A migration built a column differently from the model that describes it.",
            file=sys.stderr,
        )
        return 1

    print(f"Schema matches the models across {len(Base.metadata.sorted_tables)} tables.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
