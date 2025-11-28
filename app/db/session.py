from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from app.core.config import get_settings

settings = get_settings()

SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///./howmuch.db"

engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    future=True,
    echo=False,               # 디버그 시 True
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# 3) Base (ORM 모델이 상속)
class Base(DeclarativeBase):
    pass

# 4) FastAPI 의존성 (라우터에서 Depends로 주입)
async def get_session():
    async with SessionLocal() as s:
        yield s

# (선택) 헬스체크
async def ping():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
