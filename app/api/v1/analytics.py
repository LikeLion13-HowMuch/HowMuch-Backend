from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import SessionLocal
from app.schemas.analytics import AnalyticsRequest, AnalyticsResponse
from app.services.analytics import run_analytics  # ← 여기!

router = APIRouter(prefix="/analytics", tags=["analytics"])

async def get_session():
    async with SessionLocal() as s:
        yield s

@router.post("/summary", response_model=AnalyticsResponse)
async def analytics_summary(payload: AnalyticsRequest, session: AsyncSession = Depends(get_session)):
    try:
        return await run_analytics(session, payload.product, payload.spec.dict(), payload.region.dict())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
