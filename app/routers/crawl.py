# app/routers/crawl.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.core.config import get_settings
from app.schemas.common import MarketSource
from app.schemas.items import RawItem
from app.services.ingest import upsert_items
from app.crawlers.daangn import DaangnScraper
# from app.crawlers.joongna import JoongnaScraper
# from app.crawlers.bunjang import BunjangScraper

router = APIRouter(prefix="/crawl", tags=["crawl"])
settings = get_settings()

def get_scraper(source: MarketSource):
    if source == MarketSource.daangn:
        return DaangnScraper()
    # if source == MarketSource.joongna: return JoongnaScraper()
    # if source == MarketSource.bunjang: return BunjangScraper()
    raise HTTPException(status_code=400, detail="unsupported source")

@router.post("/daangn/keywords")
async def crawl_daangn_keywords(
    keywords: list[str] | None = Query(None, description="예: 아이폰&아이패드&맥북&애플워치&에어팟"),
    limit: int = Query(200, ge=1, le=2000, description="키워드별 최대 수집 개수"),
    session: AsyncSession = Depends(get_session),
):
    """
    당근마켓에서 기본 5개 키워드(아이폰/아이패드/맥북/애플워치/에어팟)를 검색-크롤링하고 DB에 upsert.
    - keywords 미지정 시 크롤러의 기본 키워드 사용
    - limit: 키워드별 상한
    """
    scraper = DaangnScraper()

    # 크롤 → RawItem 리스트
    rows: list[RawItem] = [
        r async for r in scraper.crawl_keywords(
            keywords=keywords if keywords else None,
            limit_per_keyword=limit,
        )
    ]

    # TODO: 키워드별로 category_id를 매핑해 주면 더 정확합니다.
    # 지금은 임시로 config의 CATEGORY_IPHONE을 기본값으로 사용.
    cnt = await upsert_items(session, rows, default_category_id=settings.CATEGORY_IPHONE)
    await session.commit()

    return {"inserted_or_updated": cnt}

# (선택) 단일 소스/단일 검색어 엔드포인트도 유지하고 싶다면:
@router.post("/{source}")
async def crawl_once(
    source: MarketSource,
    q: str = Query("", description="검색어"),
    limit: int = Query(200, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
):
    scraper = get_scraper(source)
    rows: list[RawItem] = [r async for r in scraper.search(q, limit=limit)]
    cnt = await upsert_items(session, rows, default_category_id=settings.CATEGORY_IPHONE)
    await session.commit()
    return {"source": source.value, "inserted_or_updated": cnt}
