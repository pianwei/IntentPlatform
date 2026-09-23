import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session
from .config import settings


class Base(DeclarativeBase):
    pass


def uid(prefix="id"):
    return f"{prefix}_{uuid.uuid4().hex}"


def now():
    return datetime.now(timezone.utc)


engine = create_engine(settings().database_url, pool_pre_ping=True)


def set_identity(db: Session, user_id: str):
    db.execute(text("SELECT set_config('intent.user_id', :user_id, true)"), {"user_id": user_id})


def session():
    with Session(engine, expire_on_commit=False) as db:
        yield db
