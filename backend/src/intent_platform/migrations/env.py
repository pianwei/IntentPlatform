from alembic import context
from sqlalchemy import create_engine
from intent_platform.config import settings
from intent_platform.db import Base
from intent_platform import models  # noqa: F401

if context.is_offline_mode():
    raise RuntimeError("Bootstrap migration requires a live PostgreSQL owner connection")
def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


supplied = context.config.attributes.get("connection")
if supplied is not None:
    run(supplied)
else:
    with create_engine(settings().migration_database_url).connect() as connection:
        run(connection)
