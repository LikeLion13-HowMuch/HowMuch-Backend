from typing import Sequence
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.schemas.items import RawItem

def parse_price_to_int(price_text: str | None) -> int | None:
    if not price_text: return None
    t = price_text.replace(",", "").replace("원","").replace(" ", "")
    if any(k in t for k in ["나눔","무료","가격","문의"]): return None
    digits = "".join(ch for ch in t if ch.isdigit())
    return int(digits) if digits else None

async def find_region_id(session: AsyncSession, gu: str|None, dong: str|None) -> int | None:
    if dong:
        q = text("SELECT region_id FROM emd WHERE name=:n LIMIT 1")
        r = await session.execute(q, {"n": dong})
        row = r.first()
        if row: return row[0]
    return None

async def upsert_items(session: AsyncSession, rows: Sequence[RawItem], default_category_id: int) -> int:
    sql = text("""
    INSERT INTO items (region_id, category_id, title, price, status, url, source, external_id)
    VALUES (:region_id, :category_id, :title, :price, '판매중', :url, :source, :external_id)
    ON CONFLICT ON CONSTRAINT ux_items_source_external
    DO UPDATE SET
      title = COALESCE(EXCLUDED.title, items.title),
      price = COALESCE(EXCLUDED.price, items.price),
      updated_at = NOW()
    """)
    n = 0
    for r in rows:
        price = r.price if r.price is not None else parse_price_to_int(r.price_text)
        region_id = await find_region_id(session, r.gu, r.dong)
        await session.execute(sql, {
            "region_id": region_id,
            "category_id": r.category_id or default_category_id,
            "title": r.title or "",
            "price": price or 0,
            "url": str(r.url),
            "source": r.source.value,
            "external_id": r.external_id,
        })
        n += 1
    return n
