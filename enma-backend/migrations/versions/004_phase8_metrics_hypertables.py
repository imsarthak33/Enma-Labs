"""004 — Phase 8: TimescaleDB hypertables for pipeline metrics + token usage.

Two hypertables and one regular log table land here:

* ``processing_metrics`` — per-stage latency for every doc pipeline run.
  Columns: ``recorded_at`` (PK part), ``ca_firm_id``, ``stage``, ``ms``,
  ``success``, ``document_id``. Compressed beyond 7 days, dropped beyond 90.
* ``token_usage``        — every LLM call's input / output token counts.
  Columns: ``recorded_at``, ``ca_firm_id``, ``model``, ``request_type``,
  ``input_tokens``, ``output_tokens``, ``cost_usd_micro``.
* ``error_log``          — sampled application errors with firm scope for
  per-firm dashboards. NOT a hypertable — volume is tiny.

The hypertable conversions use TimescaleDB's ``create_hypertable`` with the
1-day chunk interval, which is the canonical choice for high-cardinality
time-series at single-digit-GB/day write rates. The compression policy
matches the doc-06 retention budget (term-missing rows aren't useful past
the quarterly compliance window).

Revision ID: 004
Revises: 003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # processing_metrics — per-stage pipeline latency
    # ---------------------------------------------------------------
    op.create_table(
        "processing_metrics",
        sa.Column(
            "recorded_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ca_firm_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "stage",
            sa.String(40),
            nullable=False,
            comment="Pipeline stage label: classify | extract | verify | tax_verdict | identity",
        ),
        sa.Column(
            "ms",
            sa.Integer(),
            nullable=False,
            comment="Wall-clock latency in milliseconds.",
        ),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("ms >= 0", name="processing_metrics_ms_nonneg"),
    )
    op.create_index(
        "ix_processing_metrics_stage_time",
        "processing_metrics",
        ["stage", "recorded_at"],
    )
    op.create_index(
        "ix_processing_metrics_firm_time",
        "processing_metrics",
        ["ca_firm_id", "recorded_at"],
    )

    # ---------------------------------------------------------------
    # token_usage — per-LLM-call accounting
    # ---------------------------------------------------------------
    op.create_table(
        "token_usage",
        sa.Column(
            "recorded_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ca_firm_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column(
            "request_type",
            sa.String(40),
            nullable=False,
            comment="Logical request bucket: extraction | reasoning | embedding | layout | whisper",
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "cost_usd_micro",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Cost in micro-USD (1e-6 USD). BIGINT avoids decimal in TS index.",
        ),
        sa.CheckConstraint("input_tokens >= 0", name="token_usage_input_nonneg"),
        sa.CheckConstraint("output_tokens >= 0", name="token_usage_output_nonneg"),
    )
    op.create_index(
        "ix_token_usage_model_time",
        "token_usage",
        ["model", "recorded_at"],
    )
    op.create_index(
        "ix_token_usage_firm_time",
        "token_usage",
        ["ca_firm_id", "recorded_at"],
    )

    # ---------------------------------------------------------------
    # error_log — low-volume per-firm error sampling
    # ---------------------------------------------------------------
    op.create_table(
        "error_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column(
            "occurred_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ca_firm_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("endpoint", sa.String(120), nullable=False),
        sa.Column("error_class", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default=sa.text("'error'")),
        sa.Column(
            "request_id",
            sa.String(64),
            nullable=True,
            comment="Matches RequestIDMiddleware header for Sentry pivot.",
        ),
    )
    op.create_index(
        "ix_error_log_endpoint_time",
        "error_log",
        ["endpoint", "occurred_at"],
    )

    # ---------------------------------------------------------------
    # Hypertable conversions — gated so non-Timescale Postgres (test
    # containers, local dev without the extension) still upgrades cleanly.
    # ---------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
            PERFORM create_hypertable(
              'processing_metrics', 'recorded_at',
              chunk_time_interval => INTERVAL '1 day',
              if_not_exists => TRUE
            );
            PERFORM create_hypertable(
              'token_usage', 'recorded_at',
              chunk_time_interval => INTERVAL '1 day',
              if_not_exists => TRUE
            );

            -- Compression policy: anything older than 7 days gets compressed.
            ALTER TABLE processing_metrics
              SET (timescaledb.compress, timescaledb.compress_segmentby = 'stage');
            ALTER TABLE token_usage
              SET (timescaledb.compress, timescaledb.compress_segmentby = 'model');
            PERFORM add_compression_policy('processing_metrics', INTERVAL '7 days');
            PERFORM add_compression_policy('token_usage', INTERVAL '7 days');

            -- Retention: 90 days of detail is plenty; dashboards use rollups.
            PERFORM add_retention_policy('processing_metrics', INTERVAL '90 days');
            PERFORM add_retention_policy('token_usage', INTERVAL '90 days');
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
            PERFORM remove_retention_policy('processing_metrics', if_exists => TRUE);
            PERFORM remove_retention_policy('token_usage', if_exists => TRUE);
            PERFORM remove_compression_policy('processing_metrics', if_exists => TRUE);
            PERFORM remove_compression_policy('token_usage', if_exists => TRUE);
          END IF;
        END
        $$;
        """
    )
    op.drop_index("ix_error_log_endpoint_time", table_name="error_log")
    op.drop_table("error_log")

    op.drop_index("ix_token_usage_firm_time", table_name="token_usage")
    op.drop_index("ix_token_usage_model_time", table_name="token_usage")
    op.drop_table("token_usage")

    op.drop_index("ix_processing_metrics_firm_time", table_name="processing_metrics")
    op.drop_index("ix_processing_metrics_stage_time", table_name="processing_metrics")
    op.drop_table("processing_metrics")
