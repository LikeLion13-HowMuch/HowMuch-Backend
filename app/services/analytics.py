from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from fastapi import HTTPException

PRODUCT2CATEGORY = {"iPhone":1, "iPad":2, "MacBook":3, "AppleWatch":4, "AirPods":5}

def rows_to_dicts(rows):
    return [dict(r) for r in rows]

async def fetch_sku_id_with_fingerprint(session: AsyncSession, product: str, spec: dict) -> tuple[List[int], str]:
    """
    Build fingerprint pattern and find all SKUs that contain the spec attributes.
    Uses LIKE to match fingerprints containing all required spec parts.
    Returns list of sku_ids and model_name string.
    """
    if not spec or not spec.get("model"):
        raise ValueError("Model specification is required")
    
    model_name = product
    
    category_id = PRODUCT2CATEGORY.get(product)
    if not category_id:
        raise ValueError("Unsupported product")
    
    # 지원하는 모든 spec 필드들
    supported_fields = [
        "model", "storage", "color",           # 공통
        "chip", "ram", "screen_size",          # MacBook, iPad(screen_size)
        "size", "material", "connectivity",    # AppleWatch
        "cellular", "pencil_support"           # iPad
    ]
    
    # fingerprint 조건들을 담을 리스트
    fingerprint_conditions = []
    params: Dict[str, Any] = {}
    params["category_id"] = category_id
    
    # spec을 알파벳 순서로 정렬
    sorted_spec = sorted(spec.items(), key=lambda x: x[0])
    
    for idx, (key, value) in enumerate(sorted_spec):
        # null이거나 빈 값이면 스킵
        if not value or key not in supported_fields:
            continue
            
        model_name += " " + str(value)
        code = key.lower()
        
        # storage는 숫자만 추출하여 'storage=s:{int}' 형식
        if code == "storage":
            sval = "".join(ch for ch in str(value) if ch.isdigit())
            if not sval:  # 숫자가 없으면 스킵
                continue
            param_key = f"storage_{idx}"
            fingerprint_conditions.append(f"fingerprint LIKE :storage_{idx}")
            params[param_key] = f"%storage=i:{sval}%"
        
        # 나머지는 option_id를 DB에서 조회
        else:
            # option_id 조회
            query = text("""
                SELECT ao.option_id
                FROM attributes a
                JOIN attribute_options ao ON ao.attribute_id = a.attribute_id
                WHERE LOWER(a.code) = :code
                  AND LOWER(ao.value) = :value
                LIMIT 1
            """)
            result = await session.execute(query, {"code": code, "value": str(value).lower()})
            row = result.first()
            
            if row:
                option_id = row[0]
                param_key = f"opt_{idx}"
                fingerprint_conditions.append(f"fingerprint LIKE :{param_key}")
                params[param_key] = f"%{code}=o:{option_id}%"
            else:
                # 매칭되는 option이 없으면 빈 리스트 반환
                print(f"No option found for {code}={value}")
                return ([], model_name)
    
    # WHERE 절 구성
    where_clause = " AND ".join(fingerprint_conditions)
    
    print(f"Fingerprint WHERE: {where_clause}")
    print(f"Params: {params}")
    
    # fingerprint로 모든 매칭되는 sku_id 조회
    query = text(f"SELECT sku_id FROM sku WHERE category_id = :category_id AND {where_clause}")
    
    result = await session.execute(query, params)
    sku_ids = [row[0] for row in result.fetchall()]
    
    print(f"Found {len(sku_ids)} SKUs: {sku_ids}")
    
    if not sku_ids:
        raise ValueError(f"No SKU found matching the specifications")
    
    return (sku_ids, model_name)

async def fetch_region_id(session: AsyncSession, region: dict) -> int:
    """
    Resolve region_id from sd/sgg/emd names.
    Requires at least emd name for a unique region_id.
    """
    if not region:
        raise ValueError("Region 'emd' is required")
    
    if not region.get("emd"):
        return None

    params: Dict[str, Any] = {
        "emd": region["emd"].lower()
    }
    sd_cond = ""
    sgg_cond = ""

    if region.get("sd"):
        sd_cond = "AND LOWER(sd.name) = :sd"
        params["sd"] = region["sd"].lower()
    if region.get("sgg"):
        sgg_cond = "AND LOWER(sgg.name) = :sgg"
        params["sgg"] = region["sgg"].lower()

    q = text(f"""
        SELECT emd.region_id
        FROM emd
        JOIN sgg ON emd.sgg_id = sgg.sgg_id
        JOIN sd  ON sgg.sd_id  = sd.sd_id
        WHERE LOWER(emd.name) = :emd
          {sd_cond}
          {sgg_cond}
        LIMIT 1
    """)
    row = (await session.execute(q, params)).first()
    if not row:
        raise ValueError("Region not found with given sd/sgg/emd")
    
    print(f"Resolved region_id: {row[0]}")
    return int(row[0])

async def fetch_summary_info(session: AsyncSession, sku_ids: List[int], region_id: int | None, model_name: str) -> Dict[str, Any]:
    if not sku_ids:
        return {
            "model_name": model_name,
            "average_price": 0,
            "highest_listing_price": 0,
            "lowest_listing_price": 0,
            "listing_count": 0,
            "data_date": ""
        }
    
    # sku_ids를 IN 절로 처리
    placeholders = ",".join([f":sku_{i}" for i in range(len(sku_ids))])
    params: Dict[str, Any] = {f"sku_{i}": sku_id for i, sku_id in enumerate(sku_ids)}

    if region_id is None:
        params["sd_name"] = "서울특별시"

        q = text(f"""
        WITH latest AS (
            SELECT ps.sku_id, ps.region_id, MAX(ps.bucket_ts) AS bucket_ts
            FROM price_stats ps
            WHERE ps.sku_id IN ({placeholders})
              AND EXISTS (
                  SELECT 1
                  FROM emd
                  JOIN sgg ON emd.sgg_id = sgg.sgg_id
                  JOIN sd  ON sgg.sd_id  = sd.sd_id
                  WHERE emd.region_id = ps.region_id
                    AND LOWER(sd.name) = :sd_name
              )
            GROUP BY ps.sku_id, ps.region_id
        )
        SELECT
            SUM(ps.avg_price * ps.items_num) * 1.0 / NULLIF(SUM(ps.items_num), 0) AS avg_price,
            MAX(ps.max_price) AS max_price,
            MIN(ps.min_price) AS min_price,
            SUM(ps.items_num) AS cnt,
            MAX(ps.bucket_ts) AS data_date
        FROM price_stats ps
        JOIN latest l
          ON ps.sku_id = l.sku_id
         AND ps.region_id = l.region_id
         AND ps.bucket_ts = l.bucket_ts
        """)
    else:
        params["region_id"] = region_id

        q = text(f"""
        WITH latest AS (
            SELECT ps.sku_id, ps.region_id, MAX(ps.bucket_ts) AS bucket_ts
            FROM price_stats ps
            WHERE ps.sku_id IN ({placeholders})
            AND ps.region_id = :region_id
            GROUP BY ps.sku_id, ps.region_id
        )
        SELECT
            -- 가중 평균: SUM(avg_price * items_num) / SUM(items_num)
            COALESCE(
                CAST(
                    SUM(ps.avg_price * ps.items_num) * 1.0 / NULLIF(SUM(ps.items_num), 0)
                    AS INTEGER
                ), 
                0
            ) AS avg_price,
            COALESCE(MAX(ps.max_price), 0) AS max_price,
            COALESCE(MIN(ps.min_price), 0) AS min_price,
            COALESCE(SUM(ps.items_num), 0) AS cnt,
            COALESCE(MAX(ps.bucket_ts), '') AS data_date
        FROM price_stats ps
        JOIN latest l
        ON ps.sku_id = l.sku_id
        AND ps.region_id = l.region_id
        AND ps.bucket_ts = l.bucket_ts
        """)
    
    row = (await session.execute(q, params)).first()
    
    if not row or all(v is None for v in row):
        # 데이터가 전혀 없을 때 404 반환
        if region_id is None:
            raise HTTPException(status_code=404, detail="No price statistics found for Seoul (서울특별시).")
        else:
            raise HTTPException(status_code=404, detail=f"No price statistics found for region_id={region_id}.")

    # row 컬럼 순서: avg_price, max_price, min_price, cnt, data_date
    avg_price, max_price, min_price, cnt, data_date = row

    print(f"Summary Info - Avg: {avg_price}, Max: {max_price}, Min: {min_price}, Count: {cnt}, Date: {data_date}")

    return {
        "model_name": model_name,
        "average_price": int(avg_price or 0),
        "highest_listing_price": int(max_price or 0),
        "lowest_listing_price": int(min_price or 0),
        "listing_count": int(cnt or 0),
        "data_date": data_date or ""
    }

async def fetch_regional_analysis(session: AsyncSession, sku_ids: List[int], region: dict) -> List[Dict[str, Any]]:
    """
    같은 시군구(sgg)에 속한 모든 읍면동(emd)에 대해
    해당 sku들의 최신 price_stats 버킷 기준 가중 평균가 낮은 순으로 반환.
    average_price = SUM(avg_price * items_num) / SUM(items_num)
    listing_count = SUM(items_num)
    """
    if not sku_ids:
        return []
    
    placeholders = ",".join([f":sku_{i}" for i in range(len(sku_ids))])
    params: Dict[str, Any] = {f"sku_{i}": sku_id for i, sku_id in enumerate(sku_ids)}

    if not region:
        raise ValueError("Region is required for regional analysis")

    if not region.get("sgg") or not region.get("emd"):
        params["sd_name"] = "서울특별시"

        q = text(f"""
        -- 1) 서울특별시에 해당하는 모든 region_id 찾기
        WITH seoul_regions AS (
            SELECT emd.region_id
            FROM emd
            JOIN sgg ON emd.sgg_id = sgg.sgg_id
            JOIN sd  ON sgg.sd_id = sd.sd_id
            WHERE LOWER(sd.name) = :sd_name
        ),

        -- 2) region_id + sku_id별 최신 bucket_ts
        latest AS (
            SELECT ps.sku_id, ps.region_id, MAX(ps.bucket_ts) AS bucket_ts
            FROM price_stats ps
            WHERE ps.sku_id IN ({placeholders})
            AND ps.region_id IN (SELECT region_id FROM seoul_regions)
            GROUP BY ps.sku_id, ps.region_id
        ),

        -- 3) 최신 버킷에 대한 지역별 price stats 집계
        regional AS (
            SELECT
                ps.region_id,
                SUM(ps.avg_price * ps.items_num) AS weighted_sum,
                SUM(ps.items_num) AS total_items,
                SUM(ps.items_num) AS listing_count,
                MAX(ps.bucket_ts) AS data_date,
                MAX(ps.max_price) AS highest_listing_price,
                MIN(ps.min_price) AS lowest_listing_price
            FROM price_stats ps
            JOIN latest l
            ON ps.sku_id = l.sku_id
            AND ps.region_id = l.region_id
            AND ps.bucket_ts = l.bucket_ts
            GROUP BY ps.region_id
        )

        -- 4) emd, sgg 이름을 붙여서 반환 형식에 맞추기
        SELECT
            sgg.name AS sgg_name,
            emd.name AS emd_name,
            CAST(regional.weighted_sum * 1.0 / NULLIF(regional.total_items, 0) AS INTEGER) AS average_price,
            regional.listing_count AS listing_count
        FROM regional
        JOIN emd ON regional.region_id = emd.region_id
        JOIN sgg ON emd.sgg_id = sgg.sgg_id
        ORDER BY average_price ASC;
        """)

        rows = (await session.execute(q, params)).mappings().all()

        if not rows:
            raise HTTPException(status_code=404, detail="No price statistics found for Seoul.")

        # 매핑: SQL RowMapping -> 원하는 JSON 형식
        result = []
        for r in rows:
            sgg_name = r.get("sgg_name") or ""
            emd_name = r.get("emd_name") or ""
            avg_price = r.get("average_price")
            listing_count = r.get("listing_count")

            result.append({
                "sgg": sgg_name,
                "emd": emd_name,
                "average_price": int(avg_price) if avg_price is not None else 0,
                "listing_count": int(listing_count) if listing_count is not None else 0
            })

        return result

    params["sgg"] = region["sgg"].lower()
    
    sd_filter = ""
    if region.get("sd"):
        sd_filter = "AND LOWER(sd.name) = :sd"
        params["sd"] = region["sd"].lower()

    q = text(f"""
    WITH latest AS (
        SELECT ps.sku_id, ps.region_id, MAX(ps.bucket_ts) AS bucket_ts
        FROM price_stats ps
        WHERE ps.sku_id IN ({placeholders})
        GROUP BY ps.sku_id, ps.region_id
    )
    SELECT
        sgg.name AS sgg,
        emd.name AS emd,
        COALESCE(
            CAST(
                SUM(ps.avg_price * ps.items_num) * 1.0 / NULLIF(SUM(ps.items_num), 0)
                AS INTEGER
            ), 
            0
        ) AS average_price,
        COALESCE(SUM(ps.items_num), 0) AS listing_count
    FROM price_stats ps
    JOIN latest l ON ps.sku_id = l.sku_id AND ps.region_id = l.region_id AND ps.bucket_ts = l.bucket_ts
    JOIN emd ON ps.region_id = emd.region_id
    JOIN sgg ON emd.sgg_id = sgg.sgg_id
    JOIN sd  ON sgg.sd_id  = sd.sd_id
    WHERE ps.sku_id IN ({placeholders})
      AND LOWER(sgg.name) = :sgg
      {sd_filter}
    GROUP BY sgg.name, emd.name
    ORDER BY average_price ASC, emd.name
    """)
    rows = (await session.execute(q, params)).mappings().all()
    return [dict(r) for r in rows]

async def fetch_price_trend(session: AsyncSession, sku_ids: List[int], region_id: int | None) -> Dict[str, Any]:
    """
    현재 날짜 기준 4주 전부터 일주일 단위로 가중 평균 가격 추이 반환.
    average_price = SUM(avg_price * items_num) / SUM(items_num) (주간 단위)
    """
    if not sku_ids:
        raise HTTPException(status_code=404, detail="No SKU IDs provided")
    
    placeholders = ",".join([f":sku_{i}" for i in range(len(sku_ids))])
    params: Dict[str, Any] = {f"sku_{i}": sku_id for i, sku_id in enumerate(sku_ids)}

    if region_id is None:
        q = text(f"""
        WITH weekly_data AS (
            SELECT
                -- 주차 계산: 현재 날짜 기준 몇 주 전인지 계산
                CAST((julianday('now') - julianday(ps.bucket_ts)) / 7 AS INTEGER) AS weeks_ago,
                ps.avg_price,
                ps.items_num,
                ps.bucket_ts
            FROM price_stats ps
            WHERE ps.sku_id IN ({placeholders})
            AND julianday('now') - julianday(ps.bucket_ts) <= 28  -- 4주 이내
        )
        SELECT
            -- 주차별로 그룹화 (0 = 이번 주, 1 = 1주 전, ...)
            weeks_ago,
            -- 가중 평균: SUM(avg_price * items_num) / SUM(items_num)
            COALESCE(
                CAST(
                    SUM(avg_price * items_num) * 1.0 / NULLIF(SUM(items_num), 0)
                    AS INTEGER
                ),
                0
            ) AS price,
            -- 가장 최근 날짜를 period로 사용
            MAX(bucket_ts) AS period
        FROM weekly_data
        GROUP BY weeks_ago
        ORDER BY weeks_ago ASC
        LIMIT 4
        """)

    else:
        params["region_id"] = region_id

        q = text(f"""
        WITH weekly_data AS (
            SELECT
                -- 주차 계산: 현재 날짜 기준 몇 주 전인지 계산
                CAST((julianday('now') - julianday(ps.bucket_ts)) / 7 AS INTEGER) AS weeks_ago,
                ps.avg_price,
                ps.items_num,
                ps.bucket_ts
            FROM price_stats ps
            WHERE ps.sku_id IN ({placeholders})
            AND ps.region_id = :region_id
            AND julianday('now') - julianday(ps.bucket_ts) <= 28  -- 4주 이내
        )
        SELECT
            -- 주차별로 그룹화 (0 = 이번 주, 1 = 1주 전, ...)
            weeks_ago,
            -- 가중 평균: SUM(avg_price * items_num) / SUM(items_num)
            COALESCE(
                CAST(
                    SUM(avg_price * items_num) * 1.0 / NULLIF(SUM(items_num), 0)
                    AS INTEGER
                ),
                0
            ) AS price,
            -- 가장 최근 날짜를 period로 사용
            MAX(bucket_ts) AS period
        FROM weekly_data
        GROUP BY weeks_ago
        ORDER BY weeks_ago ASC
        LIMIT 4
        """)

    rows = (await session.execute(q, params)).mappings().all()
    # weeks_ago 역순으로 정렬되어 있으므로 다시 reverse (오래된 것부터)
    rows = [dict(r) for r in rows]

    trend_period = len(rows)
    change_rate = 0.0
    if trend_period >= 2 and rows[0]["price"] > 0:
        first, last = rows[0]["price"], rows[-1]["price"]
        change_rate = round(((last - first) / first) * 100.0, 2)

    # weeks_ago 필드 제거 (API 응답에 불필요)
    for row in rows:
        row.pop("weeks_ago", None)

    return {
        "trend_period": trend_period,
        "change_rate": change_rate,
        "chart_data": rows
    }

async def fetch_lowest_listings(session: AsyncSession, sku_ids: List[int], region_id: int, limit: int = 70) -> List[Dict[str, Any]]:
    """
    해당 region_id와 sku_ids에 해당하는 items 중 가격이 낮은 순으로 반환.
    limit개까지만 반환 (기본 70개)
    """
    if not sku_ids:
        return []
    
    placeholders = ",".join([f":sku_{i}" for i in range(len(sku_ids))])
    params: Dict[str, Any] = {f"sku_{i}": sku_id for i, sku_id in enumerate(sku_ids)}
    params["limit"] = limit

    if not region_id:
        q = text(f"""
        SELECT 
            i.price AS listing_price,
            sgg.name AS sgg,
            emd.name AS emd,
            i.source AS source,
            i.url AS source_url
        FROM items i
        JOIN emd ON i.region_id = emd.region_id
        JOIN sgg ON emd.sgg_id = sgg.sgg_id
        WHERE i.sku_id IN ({placeholders})
        AND i.status = 'active'
        ORDER BY i.price ASC
        LIMIT :limit
        """)

    else:
        params["region_id"] = region_id
        q = text(f"""
        SELECT 
            i.price AS listing_price,
            sgg.name AS sgg,
            emd.name AS emd,
            i.source AS source,
            i.url AS source_url
        FROM items i
        JOIN emd ON i.region_id = emd.region_id
        JOIN sgg ON emd.sgg_id = sgg.sgg_id
        WHERE i.sku_id IN ({placeholders})
        AND i.region_id = :region_id
        AND i.status = 'active'
        ORDER BY i.price ASC
        LIMIT :limit
        """)
    
    rows = (await session.execute(q, params)).mappings().all()
    return [dict(r) for r in rows]

async def run_analytics(session: AsyncSession, product: str, spec: dict, region: dict) -> Dict[str, Any]:
    
    sku_ids, model_name = await fetch_sku_id_with_fingerprint(session, product, spec)
    region_id = await fetch_region_id(session, region)
    summary = await fetch_summary_info(session, sku_ids, region_id, model_name)
    regional = await fetch_regional_analysis(session, sku_ids,  region)
    trend   = await fetch_price_trend(session, sku_ids, region_id)
    lowest  = await fetch_lowest_listings(session, sku_ids, region_id, limit=70)

    return {
        "status": "success",
        "data": {
            "summary_info": summary,
            "regional_analysis": { "detail_by_district": regional },
            "price_trend": trend,
            "lowest_price_listings": lowest
        }
    }
