"""SQLAlchemy declarative base. Tables are stored in the default 'public' schema."""
import enum

from sqlalchemy import Enum
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def value_enum(enum_class: type[enum.Enum]) -> Enum:
    """Enum column type that persists member *values*, as migration 0001 declares them.

    SQLAlchemy persists member names ("INBOUND") by default, which PostgreSQL
    rejects against the lowercase enum types ("inbound") the migration creates.
    """
    return Enum(enum_class, values_callable=lambda members: [m.value for m in members])
