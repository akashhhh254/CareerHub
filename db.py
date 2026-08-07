import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Absolute path avoids silently loading nothing when the script is run
# from a different working directory (e.g. some editor "run" buttons).
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

# DATABASE_URL comes from your .env file. If it isn't set, we fall back to a
# local SQLite file so the project still runs with zero database setup.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///career_hub.db")

connect_args = {}
if DATABASE_URL.startswith("mysql"):
    # TiDB Cloud (and most managed MySQL) requires SSL.
    connect_args = {"ssl": {"ssl": True}}

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(bind=engine)
base = declarative_base()
