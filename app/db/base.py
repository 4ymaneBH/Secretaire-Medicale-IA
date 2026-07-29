"""SQLAlchemy declarative base. Tables are stored in the default 'public' schema."""
from sqlalchemy.orm import declarative_base

Base = declarative_base()

