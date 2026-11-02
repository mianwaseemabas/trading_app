"""Alembic environment. Migrations run automatically at startup (app.db.run_migrations), inside
the connection passed in config.attributes, or from the CLI: `alembic -c app/migrations/alembic.ini upgrade head`."""
from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=None, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


conn = config.attributes.get("connection")
if conn is not None:
    _run(conn)
else:
    import os
    url = os.environ["DATABASE_URL"]
    eng = engine_from_config({"sqlalchemy.url": url}, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with eng.connect() as c:
        _run(c)
        c.commit()
