"""a cost rate is a date and a number

Revision ID: 0086
Revises: 0085
Create Date: 2026-09-08 04:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0086"
down_revision: Union[str, Sequence[str], None] = "0085"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Per-page costs and per-printer toner prices were stored as a single current
    value with no record of when it applied, so every cost figure PrintOps
    reported — including figures for periods that ended long ago — was computed
    at today's rates.

    That is harmless while reports only look at the current period. It stops
    being harmless the moment two periods are compared: a year-on-year cost
    comparison reprices last year at this year's toner prices and reports the
    difference as if it were a change in printing rather than a change in what
    supplies cost. The numbers look plausible and are wrong.

    So a price gets a date. The value in force now stays where it is — on the
    cartridge row and on the settings row — and gains the date it took effect;
    the periods behind it go into the two history tables below, written when a
    price changes and never updated.

    **Nothing changes for a site that has never repriced.** Both effective-from
    columns backfill to 1970-01-01: the current price is open-ended backwards,
    so every job that exists today is priced at exactly the rate it was priced
    at before this ran. Dating them from the day this shipped would instead
    leave every historical job with no rate at all, and a missing rate reads as
    free printing.
    """
    op.add_column(
        "printer_toner_cartridges",
        sa.Column("priced_from", sa.Date(), server_default="1970-01-01", nullable=False),
    )
    op.add_column(
        "report_formula_settings",
        sa.Column("rates_effective_from", sa.Date(), server_default="1970-01-01", nullable=False),
    )

    op.create_table(
        "printer_toner_price_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "printer_id",
            sa.Uuid(),
            sa.ForeignKey("printers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("color", sa.String(), nullable=False),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("yield_pages", sa.Integer(), nullable=False),
        # Half-open, [effective_from, effective_to). A closed range would price
        # the changeover day at both rates and let the database decide which.
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_printer_toner_price_history_printer_id",
        "printer_toner_price_history",
        ["printer_id"],
    )
    op.create_index(
        "ix_toner_price_history_slot",
        "printer_toner_price_history",
        ["printer_id", "color", "effective_from"],
    )

    op.create_table(
        "district_cost_rate_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cost_per_page_mono", sa.Float(), nullable=False),
        sa.Column("cost_per_page_color", sa.Float(), nullable=False),
        sa.Column("cost_per_sheet_paper", sa.Float(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema.

    Recorded price periods are lost and every cost goes back to being computed
    at whatever the current rate is. The current rates themselves are untouched,
    so no figure for the current period changes.
    """
    op.drop_table("district_cost_rate_history")
    op.drop_index("ix_toner_price_history_slot", table_name="printer_toner_price_history")
    op.drop_index(
        "ix_printer_toner_price_history_printer_id", table_name="printer_toner_price_history"
    )
    op.drop_table("printer_toner_price_history")
    op.drop_column("report_formula_settings", "rates_effective_from")
    op.drop_column("printer_toner_cartridges", "priced_from")
