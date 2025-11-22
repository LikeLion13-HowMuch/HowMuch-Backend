# -*- coding: utf-8 -*-
"""
번개장터 검색 기반 크롤링 (API 버전)
- 번개장터 API를 사용하여 상품 정보를 수집하여 데이터베이스에 저장
"""

import os
import sys
import json
import time
import random
import argparse
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from urllib.parse import quote

import requests
import pandas as pd

# ===== 설정 =====

API_BASE_URL = "https://api.bunjang.co.kr/api/1/find_v2.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# ================== 시간 변환 유틸 ==================

def format_update_time(timestamp: int) -> str:
    """
    Unix 타임스탬프를 'n분/시간/일 전' 형식으로 변환
    """
    if not timestamp:
        return "시간없음"
    
    now = datetime.now()
    update_dt = datetime.fromtimestamp(timestamp)
    delta = now - update_dt

    if delta.total_seconds() < 60:
        return f"{int(delta.total_seconds())}초 전"
    elif delta.total_seconds() < 3600:
        return f"{int(delta.total_seconds() / 60)}분 전"
    elif delta.total_seconds() < 86400:
        return f"{int(delta.total_seconds() / 3600)}시간 전"
    else:
        return f"{delta.days}일 전"

# ================== 번개장터 크롤링 로직 ==================

def fetch_bunjang_products(keyword: str, page: int, limit_per_page: int) -> List[Dict]:
    """
    번개장터 API를 호출하여 상품 목록을 가져오는 함수
    """
    params = {
        "q": keyword,
        "order": "score",
        "page": page,
        "n": limit_per_page,
        "req_ref": "search",
        "stat_device": "w",
        "version": "5",
    }
    
    print(f"🌐 번개장터 API 요청: page={page}, keyword='{keyword}'")
    
    try:
        resp = requests.get(API_BASE_URL, headers=HEADERS, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        if data.get("result") != "success":
            print(f"⚠️ API 응답 오류: {data.get('no_result_message')}")
            return []
            
        return data.get("list", [])
        
    except requests.exceptions.RequestException as e:
        print(f"❌ API 요청 실패: {e}")
        return []

def extract_product_info(item: Dict) -> Optional[Dict]:
    """
    API 응답 항목에서 필요한 정보를 추출하고 형식화하는 함수
    """
    # 광고나 비상품 항목은 제외
    if item.get("type") != "PRODUCT" and not item.get("pid"):
        return None

    pid = item.get("pid")
    
    return {
        "name": item.get("name", "상품명없음"),
        "price": int(item.get("price", 0)),
        "location": item.get("location", "지역없음"),
        "time": format_update_time(item.get("update_time")),
        "link": f"https://m.bunjang.co.kr/products/{pid}"
    }

def crawl_bunjang(keyword: str, limit: int = 100, sleep_range=(0.5, 1.5), debug: bool = False) -> List[dict]:
    """
    번개장터 상품 크롤링 오케스트레이션
    """
    results: List[dict] = []
    page = 0
    limit_per_page = 96 # 번개장터는 페이지당 약 96개 항목을 반환

    print(f"🔍 '{keyword}' 번개장터 크롤링 시작 (목표: {limit}개)...\n")

    while len(results) < limit:
        products_from_api = fetch_bunjang_products(keyword, page, limit_per_page)
        
        if not products_from_api:
            print("⚠️ 더 이상 상품이 없습니다. 크롤링 종료.")
            break

        for item in products_from_api:
            if len(results) >= limit:
                break
            
            product_info = extract_product_info(item)
            if product_info:
                results.append(product_info)
                print(
                    f"✅ ({len(results)}/{limit}) {product_info['name']} / {product_info['price']}원 / "
                    f"{product_info['location']} / {product_info['time']}"
                )
        
        page += 1
        time.sleep(random.uniform(*sleep_range))

    return results[:limit]


# ================== 실행 진입점 ==================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="번개장터 크롤러 (API 버전)")
    parser.add_argument("-k", "--keyword", type=str, default="아이폰", help="검색 키워드")
    parser.add_argument("-l", "--limit", type=int, default=100, help="수집할 상품 개수")
    parser.add_argument("-d", "--debug", action="store_true", help="디버그 모드 활성화")
    parser.add_argument("--save-db", action="store_true", help="데이터베이스에 저장")
    parser.add_argument("--category", type=str, default="iPhone", help="카테고리명 (DB 저장용)")
    parser.add_argument("--no-csv", action="store_true", help="CSV 파일 저장 안 함")

    args = parser.parse_args()

    print("=" * 60)
    print(f"번개장터 '{args.keyword}' 검색 크롤링 (API)")
    print(f"수집 개수: {args.limit}개")
    print("=" * 60 + "\n")

    data = crawl_bunjang(
        keyword=args.keyword,
        limit=args.limit,
        debug=args.debug,
    )

    if not data:
        print(f"\n'{args.keyword}' 검색 결과가 없습니다.")
        sys.exit(0)

    print("\n" + "=" * 60)
    print(f"✨ 총 {len(data)}개의 '{args.keyword}' 상품을 수집했습니다.")
    print("=" * 60 + "\n")

    # CSV 저장
    if not args.no_csv:
        df = pd.DataFrame(data)
        out_csv = f"{args.keyword}_products_bunjang.csv"
        df.to_csv(out_csv, encoding="utf-8-sig", index=False)
        print(f"📁 CSV 저장 완료: {os.path.abspath(out_csv)}")

    # 데이터베이스 저장
    if args.save_db:
        try:
            # 프로젝트 루트의 db_manager.py를 import
            from db_manager import DatabaseManager

            print("\n" + "=" * 60)
            print("💾 데이터베이스에 저장 중...")
            print("=" * 60 + "\n")

            db = DatabaseManager()
            success_count = db.insert_items_batch(
                products=data,
                marketplace_code="bunjang", # 마켓 코드
                category_name=args.category
            )
            db.close()

            print("\n" + "=" * 60)
            print(f"✅ 데이터베이스 저장 완료: {success_count}/{len(data)}개")
            print("=" * 60)

        except ImportError:
            print("❌ db_manager.py를 찾을 수 없습니다. 스크립트가 프로젝트 루트에 있는지 확인하세요.")
        except Exception as e:
            print(f"❌ 데이터베이스 저장 실패: {e}")

    print("\n🎉 크롤링 완료!")
