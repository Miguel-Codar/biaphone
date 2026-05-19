from sqlmodel import SQLModel, create_engine, Session as DBSession, select
from sqlalchemy import text
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////app/data/topphone.db")
engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})


def create_db():
    SQLModel.metadata.create_all(engine)
    # Migração manual: adiciona colunas novas em tabelas existentes
    _add_col("lead", "assigned_to",    "TEXT")
    _add_col("lead", "financeira",     "TEXT")
    _add_col("lead", "last_message_at","DATETIME")
    _add_col("lead", "notified_at",    "DATETIME")


def _add_col(table: str, column: str, col_type: str):
    with engine.connect() as conn:
        try:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            conn.commit()
            print(f"[DB] Coluna adicionada: {table}.{column}")
        except Exception:
            pass  # coluna já existe


def get_session():
    with DBSession(engine) as session:
        yield session


def get_config(key: str, default: str = "") -> str:
    from .models import Config
    with DBSession(engine) as session:
        cfg = session.exec(select(Config).where(Config.key == key)).first()
        return cfg.value if cfg else default


def set_config(key: str, value: str) -> None:
    from .models import Config
    with DBSession(engine) as session:
        cfg = session.exec(select(Config).where(Config.key == key)).first()
        if cfg:
            cfg.value = value
        else:
            cfg = Config(key=key, value=value)
        session.add(cfg)
        session.commit()
