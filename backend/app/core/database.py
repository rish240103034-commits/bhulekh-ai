from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


def _resolve_database_url(url: str) -> str:
    """Pin the PostgreSQL driver to psycopg2, which is what we install.

    SQLAlchemy 2.x defaults `postgresql://` to the psycopg (v3) DBAPI, which is
    not in our requirements. Render's managed connection string is a bare
    `postgresql://…` URL, so rewrite it to `postgresql+psycopg2://` unless the
    caller already picked a driver explicitly.
    """
    if url.startswith("postgres://"):                  # Heroku-style alias
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


settings.database_url = _resolve_database_url(settings.database_url)
_is_sqlite = settings.database_url.startswith("sqlite")

# SQLite ignores pool sizing; PostgreSQL gets a bounded, recycled pool so a long-running
# process does not accumulate stale connections behind a proxy or PgBouncer.
_engine_kwargs: dict = {"pool_pre_ping": True}
if _is_sqlite:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update(pool_size=settings.db_pool_size,
                          max_overflow=settings.db_max_overflow,
                          pool_recycle=settings.db_pool_recycle_s)

engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_sqlite_columns(base) -> None:
    """Add columns that models gained since the dev database was created.

    create_all() adds new tables but never alters existing ones, so a developer who
    upgrades would otherwise hit "no such column" and have to delete their data. Only
    additive, only SQLite; a real deployment uses Alembic against PostgreSQL.
    """
    if not settings.database_url.startswith("sqlite"):
        return
    from sqlalchemy import text

    with engine.begin() as conn:
        existing = {row[0] for row in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"))}
        for table in base.metadata.sorted_tables:
            if table.name not in existing:
                continue
            present = {row[1] for row in conn.execute(text(f"PRAGMA table_info('{table.name}')"))}
            for column in table.columns:
                if column.name in present:
                    continue
                ddl = column.type.compile(engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl}'))
