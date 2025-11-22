#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
크롤링된 아이템에서 SKU를 생성하고 가격 통계를 집계하는 스크립트

워크플로우:
1. items 테이블의 모든 아이템 조회
2. 각 아이템의 속성 값(item_attribute_values) 조회
3. 속성 조합으로 SKU 생성 (fingerprint 기반)
4. SKU별 가격 통계 집계 (지역별, 시간별)
"""

import os
import hashlib
import json
from datetime import datetime, timedelta
from collections import defaultdict
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

# .env 파일 로드
load_dotenv()

# DB 연결 정보
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "dbname": os.getenv("DB_NAME", "howmuch"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
}


def connect_db():
    """데이터베이스 연결"""
    return psycopg2.connect(**DB_CONFIG)


def generate_fingerprint(attributes_dict):
    """
    속성 조합으로 고유한 fingerprint 생성

    예: {'model': 'iPhone 15 Pro', 'capacity': '256GB', 'color': '블랙'}
        → "model:iPhone 15 Pro|capacity:256GB|color:블랙"
        → SHA256 해시
    """
    # 속성을 정렬하여 일관된 순서 보장
    sorted_attrs = sorted(attributes_dict.items())

    # 문자열로 직렬화
    attr_string = "|".join(f"{k}:{v}" for k, v in sorted_attrs)

    # SHA256 해시 생성 (앞 32자만 사용)
    hash_obj = hashlib.sha256(attr_string.encode('utf-8'))
    return hash_obj.hexdigest()[:32]


def get_item_attributes(conn, item_id):
    """
    아이템의 모든 속성 값을 조회하여 딕셔너리로 반환

    반환 예시:
    {
        'model': 'iPhone 15 Pro',
        'capacity': '256GB',
        'color': '블랙'
    }
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                a.code AS attr_code,
                a.datatype,
                iav.value_text,
                iav.value_int,
                iav.value_decimal,
                iav.value_bool,
                ao.value AS option_value
            FROM item_attribute_values iav
            JOIN attributes a ON iav.attribute_id = a.attribute_id
            LEFT JOIN attribute_options ao ON iav.option_id = ao.option_id
            WHERE iav.item_id = %s
        """, (item_id,))

        attributes = {}
        for row in cur.fetchall():
            attr_code = row['attr_code']
            datatype = row['datatype']

            # 데이터 타입에 따라 값 추출
            if row['option_value']:
                value = row['option_value']
            elif datatype == 'text':
                value = row['value_text']
            elif datatype == 'int':
                value = str(row['value_int'])
            elif datatype == 'decimal':
                value = str(row['value_decimal'])
            elif datatype == 'bool':
                value = str(row['value_bool'])
            else:
                value = None

            if value:
                attributes[attr_code] = value

        return attributes


def get_or_create_sku(conn, category_id, fingerprint, attributes_dict):
    """
    SKU를 조회하거나 생성

    반환: sku_id
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 기존 SKU 조회
        cur.execute("""
            SELECT sku_id
            FROM sku
            WHERE category_id = %s AND fingerprint = %s
        """, (category_id, fingerprint))

        result = cur.fetchone()

        if result:
            return result['sku_id']

        # 새 SKU 생성
        cur.execute("""
            INSERT INTO sku (category_id, fingerprint)
            VALUES (%s, %s)
            RETURNING sku_id
        """, (category_id, fingerprint))

        sku_id = cur.fetchone()['sku_id']
        conn.commit()

        # SKU 속성 저장
        save_sku_attributes(conn, sku_id, attributes_dict)

        return sku_id


def save_sku_attributes(conn, sku_id, attributes_dict):
    """
    SKU의 속성 값을 sku_attribute 테이블에 저장
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        for attr_code, value in attributes_dict.items():
            # 속성 ID 조회
            cur.execute("SELECT attribute_id, datatype FROM attributes WHERE code = %s", (attr_code,))
            attr = cur.fetchone()

            if not attr:
                continue

            attribute_id = attr['attribute_id']
            datatype = attr['datatype']

            # 옵션 ID 조회 (해당하는 경우)
            option_id = None
            cur.execute("""
                SELECT option_id
                FROM attribute_options
                WHERE attribute_id = %s AND value = %s
            """, (attribute_id, value))
            opt_result = cur.fetchone()
            if opt_result:
                option_id = opt_result['option_id']

            # 데이터 타입에 따라 값 저장
            value_text = value if datatype == 'text' else None
            value_int = int(value) if datatype == 'int' else None
            value_decimal = float(value) if datatype == 'decimal' else None
            value_bool = value.lower() in ['true', '1', 'yes'] if datatype == 'bool' else None

            # sku_attribute에 삽입 (중복 시 무시)
            cur.execute("""
                INSERT INTO sku_attribute
                (sku_id, attribute_id, option_id, value_text, value_int, value_decimal, value_bool)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sku_id, attribute_id) DO NOTHING
            """, (sku_id, attribute_id, option_id, value_text, value_int, value_decimal, value_bool))

        conn.commit()


def generate_skus_for_all_items():
    """
    모든 아이템에 대해 SKU 생성
    """
    print("\n" + "=" * 60)
    print("🏷️  SKU 생성 시작")
    print("=" * 60)

    conn = connect_db()

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 모든 아이템 조회
            cur.execute("SELECT item_id, category_id FROM items ORDER BY item_id")
            items = cur.fetchall()

        print(f"\n📦 총 {len(items)}개 아이템 처리 중...")

        sku_created = 0
        sku_existing = 0
        sku_map = {}  # item_id → sku_id 매핑

        for idx, item in enumerate(items, 1):
            item_id = item['item_id']
            category_id = item['category_id']

            # 아이템의 속성 조회
            attributes = get_item_attributes(conn, item_id)

            if not attributes:
                # 속성이 없는 아이템은 스킵
                continue

            # Fingerprint 생성
            fingerprint = generate_fingerprint(attributes)

            # SKU 조회 또는 생성
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT sku_id
                    FROM sku
                    WHERE category_id = %s AND fingerprint = %s
                """, (category_id, fingerprint))

                result = cur.fetchone()

                if result:
                    sku_id = result['sku_id']
                    sku_existing += 1
                else:
                    sku_id = get_or_create_sku(conn, category_id, fingerprint, attributes)
                    sku_created += 1

            # 매핑 저장
            sku_map[item_id] = sku_id

            if idx % 100 == 0:
                print(f"  처리 중: {idx}/{len(items)} ({idx * 100 // len(items)}%)")

        print(f"\n✅ SKU 생성 완료:")
        print(f"  - 새로 생성: {sku_created}개")
        print(f"  - 기존 사용: {sku_existing}개")

        return sku_map

    finally:
        conn.close()


def aggregate_price_stats(sku_map, bucket_interval='day'):
    """
    SKU별, 지역별, 시간별 가격 통계 집계

    bucket_interval: 'day', 'week', 'month'
    """
    print("\n" + "=" * 60)
    print("📊 가격 통계 집계 시작")
    print("=" * 60)

    conn = connect_db()

    try:
        # 통계 데이터 구조: (sku_id, region_id, bucket_ts) → [prices]
        stats_data = defaultdict(list)

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 모든 아이템의 가격 정보 조회
            cur.execute("""
                SELECT
                    i.item_id,
                    i.category_id,
                    i.region_id,
                    i.price,
                    i.created_at
                FROM items i
                ORDER BY i.created_at DESC
            """)

            items = cur.fetchall()

        print(f"\n📦 총 {len(items)}개 아이템 집계 중...")

        for item in items:
            item_id = item['item_id']
            region_id = item['region_id']
            price = item['price']
            created_at = item['created_at']

            # 아이템의 SKU 조회
            sku_id = sku_map.get(item_id)
            if not sku_id:
                continue

            # 시간 버킷 계산
            bucket_ts = truncate_to_bucket(created_at, bucket_interval)

            # 통계 데이터에 추가
            key = (sku_id, region_id, bucket_ts)
            stats_data[key].append(price)

        print(f"\n📈 {len(stats_data)}개 통계 버킷 생성됨")

        # price_stats 테이블에 저장
        saved_count = 0
        with conn.cursor() as cur:
            for (sku_id, region_id, bucket_ts), prices in stats_data.items():
                items_num = len(prices)
                sum_price = sum(prices)
                avg_price = sum_price / items_num
                min_price = min(prices)
                max_price = max(prices)

                cur.execute("""
                    INSERT INTO price_stats
                    (sku_id, region_id, bucket_ts, items_num, sum_price, avg_price, min_price, max_price)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (sku_id, region_id, bucket_ts)
                    DO UPDATE SET
                        items_num = EXCLUDED.items_num,
                        sum_price = EXCLUDED.sum_price,
                        avg_price = EXCLUDED.avg_price,
                        min_price = EXCLUDED.min_price,
                        max_price = EXCLUDED.max_price
                """, (sku_id, region_id, bucket_ts, items_num, sum_price, avg_price, min_price, max_price))

                saved_count += 1

            conn.commit()

        print(f"\n✅ 가격 통계 저장 완료: {saved_count}개 버킷")

        # 통계 샘플 출력
        print_stats_sample(conn)

    finally:
        conn.close()


def truncate_to_bucket(dt, interval):
    """
    날짜/시간을 버킷 단위로 절삭

    interval: 'day', 'week', 'month'
    """
    if interval == 'day':
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elif interval == 'week':
        # 주의 시작 (월요일)
        start_of_week = dt - timedelta(days=dt.weekday())
        return start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
    elif interval == 'month':
        # 월의 시작
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return dt


def print_stats_sample(conn):
    """
    통계 샘플 출력 (상위 10개)
    """
    print("\n" + "=" * 60)
    print("📊 가격 통계 샘플 (상위 10개)")
    print("=" * 60)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                ps.sku_id,
                c.name AS category_name,
                e.name AS region_name,
                ps.bucket_ts,
                ps.items_num,
                ps.avg_price,
                ps.min_price,
                ps.max_price
            FROM price_stats ps
            JOIN sku s ON ps.sku_id = s.sku_id
            JOIN category c ON s.category_id = c.category_id
            LEFT JOIN emd e ON ps.region_id = e.region_id
            ORDER BY ps.bucket_ts DESC, ps.items_num DESC
            LIMIT 10
        """)

        rows = cur.fetchall()

        if not rows:
            print("  (통계 데이터 없음)")
            return

        for row in rows:
            print(f"\n  SKU #{row['sku_id']} ({row['category_name']})")
            print(f"  지역: {row['region_name'] or '전체'}")
            print(f"  기간: {row['bucket_ts'].strftime('%Y-%m-%d')}")
            print(f"  아이템 수: {row['items_num']}개")
            print(f"  평균 가격: {int(row['avg_price']):,}원")
            print(f"  최소/최대: {row['min_price']:,}원 ~ {row['max_price']:,}원")
            print("  " + "-" * 50)


def main():
    """
    메인 실행 함수
    """
    print("=" * 60)
    print("🚀 SKU 생성 및 가격 통계 집계")
    print("=" * 60)

    # 1. SKU 생성
    sku_map = generate_skus_for_all_items()

    if not sku_map:
        print("\n⚠️  SKU를 생성할 아이템이 없습니다.")
        return

    # 2. 가격 통계 집계
    aggregate_price_stats(sku_map, bucket_interval='day')

    print("\n" + "=" * 60)
    print("🎉 처리 완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
