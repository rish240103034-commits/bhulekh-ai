from sqlalchemy import create_engine, text
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


_database_url = _resolve_database_url(settings.database_url)
_is_sqlite = _database_url.startswith("sqlite")

# SQLite ignores pool sizing; PostgreSQL gets a bounded, recycled pool so a long-running
# process does not accumulate stale connections behind a proxy or PgBouncer.
_engine_kwargs: dict = {"pool_pre_ping": True}
if _is_sqlite:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update(pool_size=settings.db_pool_size,
                          max_overflow=settings.db_max_overflow,
                          pool_recycle=settings.db_pool_recycle_s)

engine = create_engine(_database_url, **_engine_kwargs)
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
    """Add columns that models gained since the database was created.

    create_all() adds new tables but never alters existing ones, so an upgrade that
    only adds nullable columns to an existing table would otherwise hit "no such
    column" against the live schema. This helper runs after create_all() and adds
    missing nullable columns via ALTER TABLE.

    Dialect-neutral: both SQLite and PostgreSQL support `ALTER TABLE ... ADD COLUMN`
    with the same shape for nullable additions, and both are handled here (name kept
    for backward compatibility with existing imports). Only additive, never a
    rename or a drop. A real production deployment would still use Alembic for
    non-nullable changes and data migrations.
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    tables_present = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in base.metadata.sorted_tables:
            if table.name not in tables_present:
                continue
            existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_cols:
                    continue
                # Only add nullable columns automatically — anything else needs an
                # explicit migration to decide the default / backfill strategy.
                if not column.nullable:
                    continue
                col_type = column.type.compile(engine.dialect)
                # Both SQLite and PostgreSQL accept this shape; the dialect-compiled
                # type differs (TEXT vs VARCHAR etc.), which is exactly what we want.
                stmt = text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'
                )
                conn.execute(stmt)
