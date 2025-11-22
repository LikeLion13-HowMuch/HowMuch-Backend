# app/db/crud.py
from __future__ import annotations
from typing import Optional, List, Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, and_

from app.schemas.price import (
    SummaryInfo, DistrictDetail, RegionalAnalysis,
    ChartDataPoint, PriceTrend, Listing, SpecRequest, RegionRequest
)
from app.db.models import (
    Category, Sku, SkuAttribute, Attribute,
    Sd, Sgg, Emd, PriceStats, Item
)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def generate_fingerprint(spec_dict: dict) -> str:
    """
    스펙 딕셔너리로부터 SKU 식별용 fingerprint 생성

    Args:
        spec_dict: {'model': 'iPhone 13', 'color': 'Blue', 'storage': '256GB'} 형태의 사전

    Returns:
        32자 길이의 해시 문자열(전역 유일도 확보용)
    """
    import hashlib
    sorted_attrs = sorted(spec_dict.items())
    attr_string = "|".join(f"{k}:{v}" for k, v in sorted_attrs)
    return hashlib.sha256(attr_string.encode("utf-8")).hexdigest()[:32]


async def get_model_name_from_sku(db: AsyncSession, sku: Sku) -> str:
    """
    SKU의 표시용 모델명 구성

    - SKU에 연결된 속성(SkuAttribute + Attribute.code)을 읽어
      사람이 보기 좋은 문자열로 결합합니다. (필요 시 규칙을 커스터마이즈)

    Args:
        db: AsyncSession
        sku: 대상 SKU 객체

    Returns:
        모델명 문자열(예: "iPhone 13 Blue 256GB"). 속성이 없으면 "Unknown Model"
    """
    q = (
        select(SkuAttribute, Attribute)
        .join(Attribute, Attribute.attribute_id == SkuAttribute.attribute_id)
        .where(SkuAttribute.sku_id == sku.sku_id)
        .order_by(Attribute.code.asc())
    )
    rows = (await db.execute(q)).all()
    parts: List[str] = []
    for sa, at in rows:
        if sa.value_text:
            parts.append(sa.value_text)
        elif sa.value_int is not None:
            parts.append(str(sa.value_int))
        elif sa.value_bool is not None:
            parts.append("Yes" if sa.value_bool else "No")
    return " ".join(parts) if parts else "Unknown Model"


# ---------------------------------------------------------------------
# CRUD: SKU / Region
# ---------------------------------------------------------------------
async def get_sku_by_specs(db: AsyncSession, product: str, spec: SpecRequest) -> Optional[Sku]:
    """
    제품 카테고리와 스펙으로 SKU 조회

    - 카테고리 테이블에서 product 이름으로 category_id를 찾고
    - spec(모델/색상/용량 등)으로 fingerprint를 만든 뒤
    - (category_id, fingerprint) 조합으로 SKU를 조회합니다.
      (필요 시, 없으면 생성하는 upsert 로직으로 확장 가능)

    Args:
        db: Database AsyncSession
        product: 제품 카테고리명 (예: "iPhone", "MacBook", ...)
        spec: 제품 스펙(모델/색상/용량 등이 들어있는 Pydantic 모델)

    Returns:
        SKU 객체 또는 None
    """
    category = (await db.execute(select(Category).where(Category.name == product))).scalar_one_or_none()
    if not category:
        return None

    # 필드명 매핑(예: storage -> capacity) 필요 시 여기에서 처리
    field_mapping = {"storage": "capacity"}
    spec_dict = {field_mapping.get(k, k): v for k, v in spec.model_dump().items() if v is not None}
    if not spec_dict:
        return None

    fp = generate_fingerprint(spec_dict)
    q = select(Sku).where(and_(Sku.category_id == category.category_id, Sku.fingerprint == fp))
    return (await db.execute(q)).scalar_one_or_none()


async def get_region_by_name(db: AsyncSession, region: RegionRequest) -> Optional[Emd]:
    """
    RegionRequest(sd/sgg/emd)로 읍면동(Emd) 단위 지역을 조회

    - sd(시도) + sgg(시군구) + emd(읍면동) 조합으로 가장 구체적인 지역을 찾습니다.
    - 데이터가 불완전하면 None을 반환합니다.

    Args:
        db: AsyncSession
        region: RegionRequest(sd/sgg/emd 중 일부 또는 전체)

    Returns:
        Emd 객체 또는 None
    """
    if not (region.sd or region.sgg or region.emd):
        return None

    q = select(Emd).join(Sgg, Sgg.sgg_id == Emd.sgg_id)

    if region.sd:
        q = q.join(Sd, Sd.sd_id == Sgg.sd_id).where(Sd.name == region.sd)
    if region.sgg:
        q = q.where(Sgg.name == region.sgg)
    if region.emd:
        q = q.where(Emd.name == region.emd)

    return (await db.execute(q)).scalar_one_or_none()


async def get_sgg_by_name(db: AsyncSession, region: RegionRequest) -> Optional[Sgg]:
    """
    RegionRequest(sd, sgg)로 시군구(Sgg) 조회

    Args:
        db: AsyncSession
        region: RegionRequest(sd, sgg 필드 사용)

    Returns:
        Sgg 객체 또는 None
    """
    if not (region.sd and region.sgg):
        return None
    q = (
        select(Sgg)
        .join(Sd, Sd.sd_id == Sgg.sd_id)
        .where(and_(Sd.name == region.sd, Sgg.name == region.sgg))
    )
    return (await db.execute(q)).scalar_one_or_none()


# ---------------------------------------------------------------------
# CRUD: Stats / Listings
# ---------------------------------------------------------------------
async def get_summary_info(db: AsyncSession, sku_id: int, region_id: Optional[int]) -> SummaryInfo:
    """
    요약 통계 조회(판매중 기준): 평균가/최고가/최저가/매물수 + 모델명/데이터시각

    - price_stats에서 (sku_id, [region_id])의 최신 버킷 1건을 조회
    - SKU 속성을 조합해 model_name을 생성

    Args:
        db: AsyncSession
        sku_id: 대상 SKU의 기본키
        region_id: (선택) 지역 필터. None 이면 전체

    Returns:
        SummaryInfo Pydantic 모델
    """
    q = select(PriceStats).where(PriceStats.sku_id == sku_id)
    if region_id is not None:
        q = q.where(PriceStats.region_id == region_id)
    q = q.order_by(desc(PriceStats.bucket_ts)).limit(1)
    stat = (await db.execute(q)).scalar_one_or_none()

    sku = (await db.execute(select(Sku).where(Sku.sku_id == sku_id))).scalar_one_or_none()
    model_name = await get_model_name_from_sku(db, sku) if sku else "Unknown"

    if not stat:
        return SummaryInfo(
            model_name=model_name,
            average_price=0,
            highest_listing_price=0,
            lowest_listing_price=0,
            listing_count=0,
            data_date=None,
        )

    return SummaryInfo(
        model_name=model_name,
        average_price=int(stat.avg_price) if stat.avg_price is not None else 0,
        highest_listing_price=stat.max_price or 0,
        lowest_listing_price=stat.min_price or 0,
        listing_count=stat.items_num,
        data_date=stat.bucket_ts.strftime("%Y-%m-%d %H:%M"),
    )


async def get_regional_analysis(db: AsyncSession, sku_id: int, sgg_id: int) -> RegionalAnalysis:
    """
    시군구 단위(=sgg_id)로 읍면동별 최신 평균가/매물수 조회

    - sgg 내 각 emd(region_id)에 대해 price_stats의 최신 버킷을 조인
    - 동별 평균가/매물수를 DistrictDetail 리스트로 반환

    Args:
        db: AsyncSession
        sku_id: 대상 SKU의 기본키
        sgg_id: 시군구 기본키

    Returns:
        RegionalAnalysis(detail_by_district=List[DistrictDetail])
    """
    latest_subq = (
        select(
            PriceStats.region_id.label("region_id"),
            func.max(PriceStats.bucket_ts).label("max_ts"),
        )
        .join(Emd, Emd.region_id == PriceStats.region_id)
        .where(PriceStats.sku_id == sku_id, Emd.sgg_id == sgg_id)
        .group_by(PriceStats.region_id)
        .subquery()
    )

    q = (
        select(Emd.name, PriceStats.avg_price, PriceStats.items_num)
        .join(latest_subq, latest_subq.c.region_id == Emd.region_id)
        .join(
            PriceStats,
            and_(
                PriceStats.region_id == latest_subq.c.region_id,
                PriceStats.bucket_ts == latest_subq.c.max_ts,
                PriceStats.sku_id == sku_id,
            ),
        )
        .where(Emd.sgg_id == sgg_id)
        .order_by(Emd.name.asc())
    )

    rows = (await db.execute(q)).all()
    details = [
        DistrictDetail(
            emd=name,
            average_price=int(avg) if avg is not None else 0,
            listing_count=cnt or 0,
        )
        for (name, avg, cnt) in rows
    ]
    return RegionalAnalysis(detail_by_district=details)


async def get_price_trend(
    db: AsyncSession,
    sku_id: int,
    region_id: Optional[int],
    weeks: int = 7,
) -> PriceTrend:
    """
    최근 N주(또는 기간) 가격 추이 조회

    - price_stats에서 (sku_id, [region_id])를 기간 시작일 이후로 조회
    - 첫/마지막 평균가로 변화율(%) 계산
    - 시계열 그래프용 포인트(period, price) 리스트 구성

    Args:
        db: AsyncSession
        sku_id: 대상 SKU의 기본키
        region_id: (선택) 지역 필터. None이면 전체
        weeks: 조회 기간(주 단위)

    Returns:
        PriceTrend(trend_period, change_rate, chart_data)
    """
    start_dt = datetime.now(tz=timezone.utc) - timedelta(weeks=weeks)

    q = select(PriceStats).where(
        PriceStats.sku_id == sku_id,
        PriceStats.bucket_ts >= start_dt,
    )
    if region_id is not None:
        q = q.where(PriceStats.region_id == region_id)
    q = q.order_by(PriceStats.bucket_ts.asc())

    rows = (await db.execute(q)).scalars().all()
    if len(rows) < 2:
        return PriceTrend(trend_period=weeks, change_rate=0.0, chart_data=[])

    first = float(rows[0].avg_price or 0)
    last = float(rows[-1].avg_price or 0)
    change_rate = ((last - first) / first * 100.0) if first > 0 else 0.0

    chart = [
        ChartDataPoint(
            period=row.bucket_ts.strftime("%Y-%m-%d %H:%M"),
            price=int(row.avg_price) if row.avg_price is not None else 0,
        )
        for row in rows
    ]
    return PriceTrend(trend_period=weeks, change_rate=round(change_rate, 2), chart_data=chart)


async def get_lowest_price_listings(
    db: AsyncSession,
    sku_id: int,
    sgg_id: Optional[int],
    limit: int = 5,
) -> List[Listing]:
    """
    최저가 매물 N개 조회(판매중 기준)

    - items에서 sku_id(+시군구 범위)가 일치하는 레코드를 최저가 순으로 조회
    - 지역명(시군구/읍면동)과 출처/링크를 포함해 Listing 리스트로 반환
    - N+1 최소화를 위해 emd/sgg를 배치 조회

    Args:
        db: AsyncSession
        sku_id: 대상 SKU의 기본키
        sgg_id: (선택) 시군구 필터. None이면 전체
        limit: 최대 반환 개수

    Returns:
        List[Listing]
    """
    q = select(Item).where(Item.sku_id == sku_id, Item.status == "판매중")
    if sgg_id is not None:
        q = q.join(Emd, Emd.region_id == Item.region_id).where(Emd.sgg_id == sgg_id)
    q = q.order_by(Item.price.asc()).limit(limit)

    items = (await db.execute(q)).scalars().all()
    if not items:
        return []

    region_ids = {it.region_id for it in items if it.region_id is not None}
    emd_map: dict[int, Emd] = {}
    sgg_map: dict[int, Sgg] = {}

    if region_ids:
        emd_rows = (await db.execute(select(Emd).where(Emd.region_id.in_(region_ids)))).scalars().all()
        emd_map = {e.region_id: e for e in emd_rows}
        sgg_ids = {e.sgg_id for e in emd_rows}
        if sgg_ids:
            sgg_rows = (await db.execute(select(Sgg).where(Sgg.sgg_id.in_(sgg_ids)))).scalars().all()
            sgg_map = {s.sgg_id: s for s in sgg_rows}

    results: List[Listing] = []
    for it in items:
        emd = emd_map.get(it.region_id)
        sgg = sgg_map.get(emd.sgg_id) if emd else None
        district = f"{sgg.name} {emd.name}" if (sgg and emd) else (emd.name if emd else "Unknown")
        results.append(Listing(
            listing_price=it.price,
            district_detail=district,
            source=getattr(it, "source", "unknown"),
            source_url=it.url or "",
        ))
    return results
