"""Initial schema: patients, call sessions, transcripts, outcomes, appointments, patient memories

Revision ID: 0001
Revises:
Create Date: 2026-07-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    call_status = sa.Enum(
        "initiated", "ringing", "in_progress", "completed", "failed",
        "busy", "no_answer", "transferred", "canceled",
        name="callstatus",
    )
    call_direction = sa.Enum("inbound", "outbound", name="calldirection")
    outcome_type = sa.Enum(
        "resolved", "escalated", "no_answer", "voicemail",
        "transferred", "failed", "abandoned",
        name="outcometype",
    )
    transfer_reason = sa.Enum(
        "low_sentiment", "incomprehension", "duration_exceeded",
        "explicit_request", "urgent_keyword", "complex_query",
        name="transferreason",
    )
    appointment_status = sa.Enum(
        "proposed", "confirmed", "canceled", "rescheduled",
        "completed", "no_show",
        name="appointmentstatus",
    )

    op.create_table(
        "patients",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("phone_number", sa.String(20), nullable=False, unique=True, index=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("first_name", sa.String(100), nullable=True),
        sa.Column("last_name", sa.String(100), nullable=True),
        sa.Column("language_preference", sa.String(5), server_default="fr"),
        sa.Column("total_calls", sa.Integer(), server_default="0"),
        sa.Column("average_sentiment", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id"), nullable=True, index=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("duration_minutes", sa.Integer(), server_default="30"),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("status", appointment_status, nullable=False, server_default="proposed"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "call_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("call_sid", sa.String(34), nullable=False, unique=True, index=True),
        sa.Column("phone_number", sa.String(20), nullable=False, index=True),
        sa.Column("direction", call_direction, nullable=False),
        sa.Column("status", call_status, nullable=False, server_default="initiated"),
        sa.Column("language", sa.String(5), server_default="fr"),
        sa.Column("duration_seconds", sa.Integer(), server_default="0"),
        sa.Column("recording_url", sa.String(500), nullable=True),
        sa.Column("recording_consent_given", sa.Boolean(), server_default=sa.false()),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id"), nullable=True, index=True),
        sa.Column("appointment_id", sa.Integer(), sa.ForeignKey("appointments.id"), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_call_sessions_created_at", "call_sessions", ["created_at"])
    op.create_index("ix_call_sessions_status_created", "call_sessions", ["status", "created_at"])

    op.create_table(
        "transcripts",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("call_session_id", sa.Integer(), sa.ForeignKey("call_sessions.id"), nullable=False, index=True),
        sa.Column("speaker", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(5), nullable=False),
        sa.Column("sentiment_score", sa.Float(), nullable=True),
        sa.Column("sentiment_label", sa.String(20), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("intent", sa.String(100), nullable=True),
        sa.Column("entities", sa.JSON(), nullable=True),
    )
    op.create_index("ix_transcripts_call_timestamp", "transcripts", ["call_session_id", "timestamp"])

    op.create_table(
        "outcomes",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("call_session_id", sa.Integer(), sa.ForeignKey("call_sessions.id"), nullable=False, unique=True, index=True),
        sa.Column("outcome_type", outcome_type, nullable=False, index=True),
        sa.Column("outcome_details", sa.Text(), nullable=True),
        sa.Column("issue_resolved", sa.String(20), nullable=True),
        sa.Column("customer_satisfied", sa.String(20), nullable=True),
        sa.Column("transfer_reason", transfer_reason, nullable=True),
        sa.Column("transfer_to", sa.String(100), nullable=True),
        sa.Column("escalation_notes", sa.Text(), nullable=True),
        sa.Column("detected_intent", sa.String(100), nullable=True),
        sa.Column("intent_confidence", sa.String(), nullable=True),
        sa.Column("key_entities", sa.JSON(), nullable=True),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_outcomes_type_created", "outcomes", ["outcome_type", "created_at"])

    op.create_table(
        "patient_memories",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id"), nullable=False, index=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("patient_memories")
    op.drop_index("ix_outcomes_type_created", table_name="outcomes")
    op.drop_table("outcomes")
    op.drop_index("ix_transcripts_call_timestamp", table_name="transcripts")
    op.drop_table("transcripts")
    op.drop_index("ix_call_sessions_status_created", table_name="call_sessions")
    op.drop_index("ix_call_sessions_created_at", table_name="call_sessions")
    op.drop_table("call_sessions")
    op.drop_table("appointments")
    op.drop_table("patients")

    sa.Enum(name="appointmentstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="transferreason").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="outcometype").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="calldirection").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="callstatus").drop(op.get_bind(), checkfirst=True)
