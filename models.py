from sqlalchemy import Integer, String, Column, Text, ForeignKey
from db import base


class User(base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False)
    email = Column(String(120), unique=True, nullable=False)
    # Stores a werkzeug password HASH, never the raw password.
    password = Column(String(255), nullable=False)


class Report(base):
    __tablename__ = "report"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    resume_text = Column(Text)
    user_goal = Column(String(200))
    result = Column(Text)
