from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Date, Time
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

DATABASE_URL = "sqlite:///stock_assistant.db"
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False, index=True)
    stock_name = Column(String(100), nullable=False)
    sector = Column(String(50), default="")
    direction = Column(String(10), nullable=False)  # "买入" or "卖出"
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    trade_date = Column(Date, nullable=False)
    trade_time = Column(Time, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(50), unique=True, nullable=False)
    value = Column(Float, default=0.0)


def init_db():
    Base.metadata.create_all(engine)


def get_session():
    return SessionLocal()
