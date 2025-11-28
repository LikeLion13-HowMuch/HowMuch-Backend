# app/services/sku_pipeline.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Iterable, Tuple
import hashlib
import re
import logging

from sqlalchemy import select, update, text, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import (
    Item, ItemStatus,
    ItemAttributeValue, Attribute, AttributeDataType,
    Sku, SkuAttribute, PriceStats,
)

logger: logging.Logger = get_logger(__name__)


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

def _fingerprint_from_specs(specs: Dict[str, str]) -> str:
    """
    사전형 스펙에서 SKU 식별용 fingerprint 생성.

    - 키/값을 정렬하여 "k:v|k2:v2|..." 형태로 합친 후 sha256 32자.
    - 같은 스펙이면 항상 같은 fingerprint가 나옴.

    Args:
        specs: {'model_series':'iPhone 13','color':'Blue','capacity_gb':'256', ...}

    Returns:
        32자리 해시 문자열
    """
    pairs = [f"{k}:{v}" for k, v in sorted(specs.items())]
    joined = "|".join(pairs)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


_num_unit = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*(tb|gb)?\s*$', re.I)


def _normalize_numeric_str(val: str) -> Optional[int]:
    """
    '256GB', '1 TB', '512' 같은 값을 정규화하여 정수(GB 기준)로 변환.

    - TB이면 1024배
    - 숫자가 아니면 None

    Returns:
        int(GB) 또는 None
    """
    m = _num_unit.match(val or "")
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit == "tb":
        num *= 1024
    return int(num)


# ------------------------------------------------------------------------------
# Stage 1) SKU 생성/갱신 + items.sku_id 업데이트
# ------------------------------------------------------------------------------

async def _load_item_specs(session: AsyncSession, item_id: int) -> Dict[str, str]:
    """
    단일 item의 EAV 속성을 읽어, 'attribute.code'를 키로, 문자열화된 값을 값으로 하는 dict 반환.

    - Attribute.datatype 에 따라 텍스트/정수/소수/불리언/옵션을 문자열로 통일.
    - 필요 시 수치 정규화(예: '256GB' → '256')를 추가로 적용할 수 있음.

    Returns:
        {'model_series':'iPhone 13','color':'Blue','capacity_gb':'256', ...}
    """
    q = (
        select(ItemAttributeValue, Attribute)
        .join(Attribute, Attribute.attribute_id == ItemAttributeValue.attribute_id)
        .where(ItemAttributeValue.item_id == item_id)
    )
    rows = (await session.execute(q)).all()

    specs: Dict[str, str] = {}
    for iav, attr in rows:
        code = attr.code  # 예: 'model_series', 'color', 'capacity_gb'
        dt: AttributeDataType = attr.datatype

        val_str: Optional[str] = None
        if dt == AttributeDataType.text and iav.value_text is not None:
            val_str = str(iav.value_text).strip()
        elif dt == AttributeDataType.int and iav.value_int is not None:
            val_str = str(iav.value_int)
        elif dt == AttributeDataType.decimal and iav.value_decimal is not None:
            # 소수도 문자열로 고정 (fingerprint 안정성)
            val_str = f"{iav.value_decimal}".rstrip("0").rstrip(".")
        elif dt == AttributeDataType.bool and iav.value_bool is not None:
            val_str = "1" if iav.value_bool else "0"
        else:
            # enum/option 형식은 value_text로 들어왔을 것으로 가정.
            # 필요시 attribute_options 조인으로 label 값 가져와도 됨.
            if iav.value_text is not None:
                val_str = str(iav.value_text).strip()

        # 예: 용량 정규화 ('256GB' → '256')
        if code in {"capacity", "capacity_gb", "storage_gb"} and val_str:
            n = _normalize_numeric_str(val_str)
            if n is not None:
                val_str = str(n)

        if code and val_str:
            specs[code] = val_str

    return specs


async def _ensure_sku(session: AsyncSession, category_id: int, specs: Dict[str, str]) -> Tuple[int, bool]:
    """
    카테고리 + 스펙(fingerprint)으로 SKU를 조회/생성.

    - 존재하면 sku_id 반환
    - 없으면 생성 + 관련 SkuAttribute(표준화된 값) 일부 저장

    Returns:
        (sku_id, created)  created=True면 신규 생성
    """
    fp = _fingerprint_from_specs(specs)

    # 1) 기존 SKU 조회
    q = select(Sku).where(and_(Sku.category_id == category_id, Sku.fingerprint == fp))
    sku = (await session.execute(q)).scalar_one_or_none()
    if sku:
        return sku.sku_id, False

    # 2) 신규 생성
    sku = Sku(category_id=category_id, fingerprint=fp)
    session.add(sku)
    await session.flush()  # sku_id 확보

    # 3) (선택) 대표 속성들을 sku_attribute에 저장해 표시/검색에 활용
    #    - 저장 정책은 팀 규칙에 맞게 조정(모든 속성 vs 일부만)
    saved_count = 0
    for code, val in specs.items():
        # Attribute.code 로 ID 찾아 매핑
        a = (
            await session.execute(
                select(Attribute).where(Attribute.code == code)
            )
        ).scalar_one_or_none()
        if not a:
            continue

        sa = SkuAttribute(
            sku_id=sku.sku_id,
            attribute_id=a.attribute_id,
            value_text=val,  # 간단히 텍스트로 보관(정렬 필요하면 타입 나눠서 저장)
        )
        session.add(sa)
        saved_count += 1

    logger.debug("Created SKU %s (category=%s, attrs=%d)", sku.sku_id, category_id, saved_count)
    return sku.sku_id, True


async def ensure_sku_for_items(session: AsyncSession, limit: Optional[int] = None) -> int:
    """
    items의 속성(EAV)을 묶어 SKU를 생성/갱신하고, items.sku_id를 채운다.

    - 대상: sku_id 가 NULL 인 아이템(또는 limit가 있으면 상위 N개)
    - 스펙 → fingerprint → SKU upsert → items.sku_id 업데이트

    Args:
        session: AsyncSession
        limit: 처리할 최대 개수(없으면 전부)

    Returns:
        처리한 item 개수
    """
    # 대상 item 조회
    q = select(Item.item_id, Item.category_id).where(Item.sku_id.is_(None))
    if limit:
        q = q.limit(limit)
    rows = (await session.execute(q)).all()
    if not rows:
        logger.info("SKU 대상 item 없음 (sku_id IS NULL).")
        return 0

    updated = 0
    for item_id, category_id in rows:
        specs = await _load_item_specs(session, item_id)
        if not specs:
            # 속성이 하나도 없으면 SKU를 만들지 않음
            continue

        sku_id, created = await _ensure_sku(session, category_id, specs)

        # items.sku_id 업데이트
        await session.execute(
            update(Item).where(Item.item_id == item_id).values(sku_id=sku_id)
        )
        updated += 1

    logger.info("SKU 매핑 완료: %d개 items 갱신", updated)
    return updated


# ------------------------------------------------------------------------------
# Stage 2) price_stats 적재/업데이트 (판매중만)
# ------------------------------------------------------------------------------

@dataclass
class StatsOptions:
    """
    통계 적재 옵션.
    """
    bucket: str = "day"          # 'hour' | 'day' | 'week' | 'month'
    timezone: str = "Asia/Seoul" # 버킷 산정 타임존


async def refresh_price_stats(session: AsyncSession, options: StatsOptions = StatsOptions()) -> int:
    """
    `items`에서 판매중만 골라 버킷(date_trunc) 단위로 price_stats에 업서트.

    - 집계: COUNT, SUM, AVG, MIN, MAX
    - 키: (sku_id, region_id, bucket_ts)
    - 타임존 기준 버킷 산정: date_trunc(bucket, created_at AT TIME ZONE :tz)
    - 이미 존재하면 DO UPDATE 로 교체

    Args:
        session: AsyncSession
        options: StatsOptions(bucket, timezone)

    Returns:
        upsert된 그룹 개수(추정). (실제 변경 건수는 드라이버/버전에 따라 0 반환될 수 있음)
    """
    # ItemStatus가 Enum이면 active 값 문자열을 얻고, 아니면 '판매중' 등 문자열 직접 사용
    active_value = None
    try:
        active_value = ItemStatus.active  # Enum
    except Exception:
        active_value = "판매중"           # 문자열 상태를 쓴다면 팀 규칙에 맞게 조정

    # 일부 드라이버는 rowcount를 정확히 돌려주지 않아서 "추정치"임
    sql = text(f"""
        INSERT INTO price_stats (sku_id, region_id, bucket_ts, items_num, sum_price, avg_price, min_price, max_price)
        SELECT
            i.sku_id,
            i.region_id,
            date_trunc(:bucket, i.created_at AT TIME ZONE :tz)::timestamptz AS bucket_ts,
            COUNT(*) AS items_num,
            SUM(i.price) AS sum_price,
            AVG(i.price)::numeric(12,2) AS avg_price,
            MIN(i.price) AS min_price,
            MAX(i.price) AS max_price
        FROM items AS i
        WHERE
            i.sku_id IS NOT NULL
            AND i.region_id IS NOT NULL
            AND i.status = :active
        GROUP BY 1, 2, 3
        ON CONFLICT (sku_id, region_id, bucket_ts)
        DO UPDATE SET
            items_num = EXCLUDED.items_num,
            sum_price = EXCLUDED.sum_price,
            avg_price = EXCLUDED.avg_price,
            min_price = EXCLUDED.min_price,
            max_price = EXCLUDED.max_price;
    """)

    res = await session.execute(
        sql,
        {
            "bucket": options.bucket,
            "tz": options.timezone,
            "active": active_value,
        },
    )
    # 주의: asyncpg + SQLAlchemy에선 rowcount가 의미 없을 수 있음
    logger.info("price_stats 업서트 완료 (bucket=%s, tz=%s)", options.bucket, options.timezone)
    return res.rowcount if res.rowcount is not None else 0


# ------------------------------------------------------------------------------
# Pipeline entrypoint
# ------------------------------------------------------------------------------

async def run_pipeline(session: AsyncSession, ensure_sku_limit: Optional[int] = None, *,
                       bucket: str = "day", timezone: str = "Asia/Seoul") -> None:
    """
    전체 파이프라인 실행:
      1) SKU 미지정 item에 대한 SKU 생성/매핑
      2) 판매중만 대상으로 price_stats 집계 업서트

    Args:
        session: AsyncSession (FastAPI DI 또는 수동 생성)
        ensure_sku_limit: SKU 매핑 단계에서 처리할 최대 item 개수(없으면 전부)
        bucket: date_trunc 버킷 (hour/day/week/month)
        timezone: 버킷 산정 타임존
    """
    logger.info("=== SKU/Stats 파이프라인 시작 ===")
    # 1) SKU 매핑
    cnt = await ensure_sku_for_items(session, limit=ensure_sku_limit)
    logger.info("SKU 매핑 건수: %d", cnt)

    # 2) price_stats 업서트
    affected = await refresh_price_stats(session, StatsOptions(bucket=bucket, timezone=timezone))
    logger.info("price_stats 업서트 완료(추정 rowcount=%s)", affected)

    await session.commit()
    logger.info("=== SKU/Stats 파이프라인 종료 ===")
