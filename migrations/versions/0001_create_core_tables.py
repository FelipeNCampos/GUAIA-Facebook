"""create core tables

Revision ID: 0001_create_core_tables
Revises:
Create Date: 2026-05-05 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_create_core_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "face_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("id_query", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("query_source", sa.String(length=128), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status_current", sa.String(length=64), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("id_query", name="uq_face_jobs_id_query"),
    )
    op.create_index("ix_face_jobs_status_current", "face_jobs", ["status_current"])

    op.create_table(
        "face_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("id_query", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("url_normalized", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="discovered"),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_face_records_id_query", "face_records", ["id_query"])
    op.create_index("ix_face_records_status", "face_records", ["status"])
    op.create_index("ix_face_records_category", "face_records", ["category"])

    op.create_table(
        "face_job_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("id_query", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_face_job_events_id_query", "face_job_events", ["id_query"])

    op.create_table(
        "face_exports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("id_query", sa.String(length=64), nullable=False),
        sa.Column("export_format", sa.String(length=16), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_face_exports_id_query", "face_exports", ["id_query"])

    op.create_table(
        "face_session_cookies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_name", sa.String(length=128), nullable=False),
        sa.Column("cookie_name", sa.String(length=256), nullable=False),
        sa.Column("cookie_value", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=256), nullable=False),
        sa.Column("path", sa.String(length=256), nullable=False, server_default="/"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("invalid_reason", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_face_session_cookies_profile_name",
        "face_session_cookies",
        ["profile_name"],
    )
    op.create_index("ix_face_session_cookies_is_active", "face_session_cookies", ["is_active"])

    op.create_table(
        "face_search_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cache_key", sa.String(length=512), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_face_search_cache_cache_key",
        "face_search_cache",
        ["cache_key"],
        unique=True,
    )

    op.create_table(
        "face_recent_results_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cache_key", sa.String(length=512), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_face_recent_results_cache_cache_key",
        "face_recent_results_cache",
        ["cache_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_face_recent_results_cache_cache_key", table_name="face_recent_results_cache")
    op.drop_table("face_recent_results_cache")
    op.drop_index("ix_face_search_cache_cache_key", table_name="face_search_cache")
    op.drop_table("face_search_cache")
    op.drop_index("ix_face_session_cookies_is_active", table_name="face_session_cookies")
    op.drop_index("ix_face_session_cookies_profile_name", table_name="face_session_cookies")
    op.drop_table("face_session_cookies")
    op.drop_index("ix_face_exports_id_query", table_name="face_exports")
    op.drop_table("face_exports")
    op.drop_index("ix_face_job_events_id_query", table_name="face_job_events")
    op.drop_table("face_job_events")
    op.drop_index("ix_face_records_category", table_name="face_records")
    op.drop_index("ix_face_records_status", table_name="face_records")
    op.drop_index("ix_face_records_id_query", table_name="face_records")
    op.drop_table("face_records")
    op.drop_index("ix_face_jobs_status_current", table_name="face_jobs")
    op.drop_table("face_jobs")
