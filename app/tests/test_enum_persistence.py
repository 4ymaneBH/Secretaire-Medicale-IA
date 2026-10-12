"""Enum columns must persist the lowercase values the Alembic migration declares.

Migration 0001 creates PostgreSQL enum types whose labels are the Python enum
*values* ("inbound", "confirmed", ...). SQLAlchemy persists enum *names*
("INBOUND", "CONFIRMED", ...) unless told otherwise, which PostgreSQL rejects
with "invalid input value for enum". SQLite accepts any string, so these tests
check the labels themselves rather than relying on an insert failing.
"""
import pytest
from sqlalchemy import Enum, text

from app.models import Appointment, CallSession, Outcome
from app.models.appointment import AppointmentStatus
from app.models.call_session import CallDirection, CallStatus, Patient

ENUM_COLUMNS = [
    (CallSession.__table__.c.direction, CallDirection),
    (CallSession.__table__.c.status, CallStatus),
    (Appointment.__table__.c.status, AppointmentStatus),
    (Outcome.__table__.c.outcome_type, Outcome.outcome_type.type.enum_class),
    (Outcome.__table__.c.transfer_reason, Outcome.transfer_reason.type.enum_class),
]


@pytest.mark.parametrize(
    ("column", "enum_class"),
    ENUM_COLUMNS,
    ids=[f"{c.table.name}.{c.name}" for c, _ in ENUM_COLUMNS],
)
def test_enum_column_labels_are_the_migration_values(column, enum_class):
    assert isinstance(column.type, Enum)
    assert column.type.enums == [member.value for member in enum_class]


@pytest.mark.asyncio
async def test_call_session_row_stores_lowercase_labels(db_session):
    patient = Patient(phone_number="+15550005555", language_preference="fr")
    db_session.add(patient)
    await db_session.flush()

    db_session.add(CallSession(
        call_sid="CA_enum_labels",
        phone_number=patient.phone_number,
        direction=CallDirection.INBOUND,
        status=CallStatus.IN_PROGRESS,
        patient_id=patient.id,
    ))
    await db_session.commit()

    row = (await db_session.execute(
        text("SELECT direction, status FROM call_sessions WHERE call_sid = 'CA_enum_labels'")
    )).one()

    assert tuple(row) == ("inbound", "in_progress")
