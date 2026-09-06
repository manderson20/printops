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

  * every table and column a model declares must exist,
  * a column the model gives a server default must have one there, and
  * nullability must agree.

Run against a database already at head. Exits non-zero and prints every
disagreement.
"""

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Check the tree this script lives in, whatever else is installed.
#
# Running `python scripts/check_schema_matches_models.py` puts *scripts/* on
# sys.path[0] and does not add the working directory at all, so `import app`
# falls through to whatever the environment happens to provide. On a box with
# an editable install pointing somewhere else — a git worktree sharing a venv
# with the main checkout, which is exactly the setup here — that is a different
# copy of the models, and this script then cheerfully reports that a database
# matches models it never looked at.
#
# Caught when 0080 added two tables, the checker reported "matches across 37
# tables" against a database that had neither, and the number never moved. A
# guard that silently validates the wrong code is worse than no guard, because
# the green tick is believed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Importing the package is what registers every model on the metadata;
# taking Base from it rather than from app.models.base means the name is
# genuinely used, instead of an import that only looks unused to a reader
# and to CodeQL.
from app.models import Base  # noqa: E402


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

    tables_present = {table for table, _ in actual}

    problems: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in tables_present:
            # A model whose table no migration creates at all. Reported
            # once for the table rather than once per column, and not
            # skipped: it is the same drift, just further along.
            problems.append(f"{table.name}: the model has this table and the database does not")
            continue
        for column in table.columns:
            found = actual.get((table.name, column.name))
            if found is None:
                problems.append(
                    f"{table.name}.{column.name}: the model has this column and the "
                    f"database does not — every query naming it will fail"
                )
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
