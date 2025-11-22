# app/db/session.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from app.core.config import get_settings

settings = get_settings()

# 1) Engine
engine = create_async_engine(str(settings.DATABASE_URL), pool_pre_ping=True)

# 2) Session factory
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

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
