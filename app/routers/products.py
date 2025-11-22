# app/routers/products.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.price import (
    ProductPriceRequest,
    ProductPriceResponse,
    ProductPriceData,
)
from app.db import crud  # async crud 함수들

router = APIRouter(prefix="/products", tags=["products"])

@router.post("/price", response_model=ProductPriceResponse)
async def get_product_price(
    request: ProductPriceRequest,
    db: AsyncSession = Depends(get_session),
):
    """
    제품 스펙(모델/색상/용량 등) + 지역(시/군/구/동)으로
    요약, 지역분석, 가격추이, 최저가 리스트를 묶어 반환
    """
    try:
        # 1) SKU 식별
        sku = await crud.get_sku_by_specs(db, request.product, request.spec)
        if not sku:
            return ProductPriceResponse(status="error", message="조건에 맞는 제품이 없습니다.")

        # 2) 지역 식별(읍면동/시군구)
        region = await crud.get_region_by_name(db, request.region)
        region_id = region.region_id if region else None

        sgg = await crud.get_sgg_by_name(db, request.region)
        sgg_id = sgg.sgg_id if sgg else None

        # 3) 요약 통계(평균/최고/최저/개수) — 판매중 기준
        summary = await crud.get_summary_info(db, sku_id=sku.sku_id, region_id=region_id)

        # 4) 지역 분석(시군구 묶음 → 동별 평균가 TOP/N)
        regional_analysis = None
        if sgg_id is not None:
            regional_analysis = await crud.get_regional_analysis(db, sku_id=sku.sku_id, sgg_id=sgg_id)

        # 5) 가격 추이(최근 n주/일) — price_stats 기반
        price_trend = await crud.get_price_trend(db, sku_id=sku.sku_id, region_id=region_id, weeks=8)

        # 6) 최저가 매물 N개(출처/링크 포함)
        lowest_listings = await crud.get_lowest_price_listings(
            db, sku_id=sku.sku_id, sgg_id=sgg_id, limit=5
        )

        return ProductPriceResponse(
            status="success",
            data=ProductPriceData(
                summary_info=summary,
                regional_analysis=regional_analysis,
                price_trend=price_trend,
                lowest_price_listings=lowest_listings,
            ),
        )

    except HTTPException:
        raise
    except Exception as e:
        # 로깅 추가 가능
        raise HTTPException(status_code=500, detail=f"서버 오류: {e}")

@router.get("/health")
async def health_check():
    return {"ok": True}

