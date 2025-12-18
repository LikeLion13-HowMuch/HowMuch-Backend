# joongna_seoul_crawler.py
# -*- coding: utf-8 -*-

import re
import csv
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import argparse


BASE_URL = "https://web.joongna.com"

############################################################
# 0. 서울 25개 구, 카테고리 맵
############################################################

SEOUL_DISTRICTS = [
    "강남구", "강동구", "강북구", "강서구",
    "관악구", "광진구", "구로구", "금천구",
    "노원구", "도봉구", "동대문구", "동작구",
    "마포구", "서대문구", "서초구", "성동구",
    "성북구", "송파구", "양천구", "영등포구",
    "용산구", "은평구", "종로구", "중구", "중랑구",
]

CATEGORY_MAP = {
    "아이폰": 1,
    "아이패드": 2,
    "맥북": 3,
    "애플워치": 4,
    "에어팟": 5,
}

IPHONE, IPAD, MACBOOK, APPLE_WATCH, AIRPODS = 1, 2, 3, 4, 5


############################################################
# 1. 광고 필터 (삽니다 / 수리 등)
############################################################

BUYING_HINTS = [
    "삽니다", "구매합니다", "구해요", "찾습니다",
    "매입", "고가매입", "최고가매입", "당일매입",
    "매입해요", "매입합니다", "고가매수",
]

SERVICE_HINTS = [
    "수리", "교체", "수선", "출장수리", "사설수리",
    "위탁판매", "대여", "렌탈", "보험", "as", "a/s",
]


def is_advertisement(title: str) -> bool:
    t = title.lower().replace(" ", "")
    if any(k.replace(" ", "") in t for k in BUYING_HINTS):
        return True
    if any(k.replace(" ", "") in t for k in SERVICE_HINTS):
        return True
    return False


############################################################
# 2. 액세서리 필터 관련 유틸
############################################################

def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).lower()


def _contains_any(text: str, keywords: List[str]) -> bool:
    t = _norm(text)
    return any(_norm(kw) in t for kw in keywords)


ACCESSORY_CORE: Dict[int, List[str]] = {
    IPHONE: ["케이스", "범퍼", "젤리", "실리콘", "필름", "보호필름", "강화유리", "거치대", "스탠드",
             "팝소켓", "링", "스트랩", "충전기", "케이블", "라이트닝", "type-c", "어댑터", "배터리팩",
             "보조배터리", "무선충전기", "도킹", "도킹스테이션", "magsafe case", "magnetic case",
             "case", "bumper", "jelly", "silicone", "film", "protector", "screen protector",
             "holder", "dock", "charger", "cable", "adapter"],
    IPAD: ["케이스", "커버", "스마트커버", "폴리오", "키보드케이스", "키보드", "펜슬팁", "펜촉",
           "필름", "강화유리", "거치대", "스탠드", "크래들", "충전기", "케이블", "paperlike",
           "smart cover", "folio", "pencil tip", "holder", "dock", "stand", "charger"],
    MACBOOK: ["파우치", "슬리브", "케이스", "하드케이스", "키스킨", "키보드 스킨", "키캡", "필름",
              "강화유리", "스탠드", "거치대", "독", "허브", "usb 허브", "type-c 허브", "도킹스테이션",
              "어댑터", "충전기", "연장케이블", "쿨러", "쿨링패드", "sleeve", "pouch", "shell case",
              "keyboard cover", "dock", "hub", "adapter", "stand", "cooler"],
    APPLE_WATCH: ["밴드", "스트랩", "가죽밴드", "메탈밴드", "나이키밴드", "케이스", "범퍼",
                  "보호필름", "강화유리", "충전기", "충전독", "충전스탠드", "band", "strap", "loop",
                  "link", "case", "bumper", "film", "charger", "dock", "stand"],
    AIRPODS: ["케이스", "실리콘케이스", "하드케이스", "가죽케이스", "키링", "이어팁", "폼팁",
              "스트랩", "충전기", "충전케이블", "충전케이스(빈 케이스)", "보호필름",
              "case", "tip", "ear tip", "foam tip", "strap", "charger"],
}

DEVICE_STRONG_HINTS: Dict[int, List[str]] = {
    IPHONE: ["본체", "풀박스", "영수증", "자급제", "미개봉", "리퍼", "공기계", "정품등록", "아이클라우드", "icloud",
             "배터리성능", "배터리 사이클", "사이클", "개통", "유심", "용량", "128gb", "256gb", "512gb", "1tb"],
    IPAD: ["본체", "풀박스", "영수증", "자급제", "미개봉", "리퍼", "wifi", "cellular", "lte",
           "용량", "128gb", "256gb", "512gb", "1tb", "2tb", "11형", "12.9", "10.9", "10.2", "9.7"],
    MACBOOK: ["본체", "풀박스", "영수증", "m1", "m2", "m3", "intel", "i5", "i7", "ram", "ssd", "배터리 사이클", "사이클",
              "13인치", "14인치", "15인치", "16인치"],
    APPLE_WATCH: ["본체", "풀박스", "울트라", "se", "gps", "cellular", "나이키", "41mm", "45mm", "49mm", "40mm", "44mm",
                  "stainless", "aluminum", "티타늄"],
    AIRPODS: ["본체", "충전케이스 포함", "미개봉", "정품 등록", "정품 시리얼", "시리얼", "case 포함"],
}

ACCESSORY_ONLY_HINTS: List[str] = [
    "전용", "호환", "for", "용", "단품",
    "케이스만", "필름만", "스트랩만", "밴드만", "케이블만",
    "충전케이스 단품", "충전기만", "허브만", "독만",
    "stand only", "case only", "band only",
]

INCLUSION_PHRASES: List[str] = [
    "케이스 포함", "필름 부착", "필름 붙임", "사은품",
    "덤으로", "증정", "케이스 드림", "필름 드림",
]


def is_accessory_title(
    title: str,
    category_id: int,
    price: Optional[int] = None,
    baseline_mean: Optional[float] = None,
) -> bool:
    """
    True → 액세서리(제외) / False → 본체(통과)
    """
    if not title:
        return False

    # 액세서리 핵심 단어가 없으면 본체
    if not _contains_any(title, ACCESSORY_CORE.get(category_id, [])):
        return False

    # '덤/포함' 표현이 있으면 본체 판매 가능성 → 본체
    if _contains_any(title, INCLUSION_PHRASES):
        return False

    # 액세서리-전용 신호 → 강하게 액세서리
    if _contains_any(title, ACCESSORY_ONLY_HINTS):
        return True

    # 본체 강한 힌트가 있으면 본체
    if _contains_any(title, DEVICE_STRONG_HINTS.get(category_id, [])):
        return False

    # 가격 힌트: baseline 대비 극저가면 액세서리로 가중
    if price is not None and baseline_mean:
        if price < max(50_000, baseline_mean * 0.25):
            return True

    # 기본: 액세서리로 간주
    return True


############################################################
# 3. 카테고리별 가격 가드
############################################################

CATEGORY_PRICE_GUARD = {
    IPHONE:      {"min": 30_000,   "max": 5_000_000},
    IPAD:        {"min": 30_000,   "max": 4_000_000},
    MACBOOK:     {"min": 100_000,  "max": 8_000_000},
    APPLE_WATCH: {"min": 20_000,   "max": 2_000_000},
    AIRPODS:     {"min": 10_000,   "max": 800_000},
}


############################################################
# 4. 상대 시간 → UTC 변환 + 위치 파싱
############################################################

REL_TIME_PAT = re.compile(r"(\d+)\s*(초|분|시간|일|주|개월|달)\s*전")


def parse_relative_time_to_utc(rel: str) -> Optional[datetime]:
    if not rel:
        return None
    m = REL_TIME_PAT.search(rel)
    if not m:
        return None

    val = int(m.group(1))
    unit = m.group(2)

    if unit == "초":
        delta = timedelta(seconds=val)
    elif unit == "분":
        delta = timedelta(minutes=val)
    elif unit == "시간":
        delta = timedelta(hours=val)
    elif unit == "일":
        delta = timedelta(days=val)
    elif unit == "주":
        delta = timedelta(days=7 * val)
    elif unit in ("개월", "달"):
        delta = timedelta(days=30 * val)
    else:
        return None

    return datetime.utcnow() - delta


def extract_location_and_time(li: BeautifulSoup):
    """
    li 하나에서 '논현1동', '1시간 전' 같은 위치/시간 텍스트 추출
    """
    location_text = None
    time_text = None

    info_div = li.select_one("div.mt-1.mb-2, div[class*='mt-1'][class*='mb-2']")
    if info_div:
        spans = info_div.find_all("span")
    else:
        spans = li.find_all("span")

    for span in spans:
        txt = span.get_text(strip=True)
        if not txt or txt == "|":
            continue

        if REL_TIME_PAT.search(txt):
            time_text = txt
        elif any(suffix in txt for suffix in ["동", "구", "시", "읍", "면", "리"]) and "전" not in txt:
            if location_text is None:
                location_text = txt

    return location_text, time_text


def split_admin_from_location(
    raw_location: Optional[str],
    sd_hint: Optional[str] = None,
    sgg_hint: Optional[str] = None,
):
    """
    raw_location 예:
      - '논현1동'
      - '강남구 역삼동'
      - '서울 강남구 역삼동'
    를 (sd, sgg, emd) 로 분리. hint 있으면 우선 사용.
    """
    sd = sd_hint or ""
    sgg = sgg_hint or ""
    emd = ""

    if not raw_location:
        return sd, sgg, emd

    parts = raw_location.split()
    if len(parts) == 1:
        emd = parts[0]
    elif len(parts) == 2:
        if not sgg:
            sgg = parts[0]
        emd = parts[1]
    else:
        if not sd:
            sd = parts[0]
        if not sgg:
            sgg = parts[1]
        emd = parts[-1]

    return sd, sgg, emd


############################################################
# 5. Selenium 드라이버
############################################################

def create_driver(headless: bool = True) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    return webdriver.Chrome(options=opts)


############################################################
# 6. HTML 파서(한 페이지)
############################################################

def parse_joongna_search_html(
    html: str,
    category_id: int,
    sd_hint: Optional[str],
    sgg_hint: Optional[str],
    last_crawled_at_iso: str,
):
    """
    검색 결과 HTML 한 페이지 → 스펙에 맞는 dict 리스트.
    """
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict] = []

    grid = soup.select_one("ul.grid")
    if not grid:
        print("⚠️ 상품 리스트(ul.grid)를 찾지 못했습니다.")
        return [], False  # items, found_any_product

    found_any_product = False

    for li in grid.select("li"):
        a_tag = li.select_one("a[href*='/product/']")
        if not a_tag:
            continue

        href = a_tag.get("href", "")
        if not href or "/product/form" in href:
            continue

        found_any_product = True

        url = urljoin(BASE_URL, href)
        external_id = url.split("/")[-1].split("?")[0]

        # 제목
        title_tag = li.select_one("h2, p.font-semibold, p.line-clamp-2")
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            continue

        # 광고 필터
        if is_advertisement(title):
            continue

        # 가격
        price_tag = li.select_one("div.font-semibold, p.text-gray-900, p[class*='price']")
        raw_price = price_tag.get_text(strip=True) if price_tag else ""
        digits = re.sub(r"[^0-9]", "", raw_price)
        price = int(digits) if digits else 0

        # 카테고리별 가격 가드
        guard = CATEGORY_PRICE_GUARD.get(category_id)
        if guard:
            if price < guard["min"] or price > guard["max"]:
                continue

        # 액세서리 필터
        if is_accessory_title(title, category_id, price, baseline_mean=None):
            continue

        # 위치/시간
        loc_text, rel_time_text = extract_location_and_time(li)
        sd, sgg, emd = split_admin_from_location(loc_text, sd_hint=sd_hint, sgg_hint=sgg_hint)

        posted_at_iso = ""
        if rel_time_text:
            dt = parse_relative_time_to_utc(rel_time_text)
            if dt:
                posted_at_iso = dt.isoformat(timespec="seconds") + "Z"

        posted_updated_at_iso = ""  # 정보 없음

        item = {
            "source": "joongna",
            "external_id": external_id,
            "category_id": category_id,
            "title": title,
            "price": price,
            "url": url,
            "status": "active",
            "sd": sd,
            "sgg": sgg,
            "emd": emd,
            "posted_at": posted_at_iso,
            "posted_updated_at": posted_updated_at_iso,
            "last_crawled_at": last_crawled_at_iso,
        }

        items.append(item)

    return items, found_any_product


############################################################
# 7. 키워드(구 + 카테고리명) 단위 크롤링
############################################################

def crawl_keyword(
    driver: webdriver.Chrome,
    keyword: str,
    category_id: int,
    sd_hint: str,
    sgg_hint: str,
    max_pages: int = 20,
    sleep_range: tuple = (0.5, 1.5),
):
    """
    예) keyword = '강남구 아이폰'
    최신순 정렬 + 페이지 끝까지.
    """
    encoded = quote(keyword)
    base_url = f"{BASE_URL}/search/{encoded}?keywordSource=INPUT_KEYWORD&sort=RECENT_SORT"

    all_items: List[Dict] = []
    last_crawled_at_iso = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    for page in range(1, max_pages + 1):
        if page == 1:
            url = base_url
        else:
            url = f"{base_url}&page={page}"

        print(f"[PAGE] {url}")
        driver.get(url)

        # 상품 리스트 로딩 기다리기
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "ul.grid li"))
            )
        except Exception:
            print("  ⚠️ ul.grid li 를 찾지 못했습니다. 이 페이지는 비어있는 것으로 처리하고 중단.")
            break

        time.sleep(1.5)

        html = driver.page_source
        page_items, found_any_product = parse_joongna_search_html(
            html=html,
            category_id=category_id,
            sd_hint=sd_hint,
            sgg_hint=sgg_hint,
            last_crawled_at_iso=last_crawled_at_iso,
        )

        # 이 페이지에서 상품 카드 자체가 하나도 없으면 → 이 (구, 카테고리) 끝.
        if not found_any_product:
            print("  ⚠️ 더 이상 상품 카드가 없습니다. 다음 키워드로 이동.")
            break

        print(f"  ✅ 유효 상품 {len(page_items)}개 추출")
        all_items.extend(page_items)

        # 페이지별 딜레이
        time.sleep((sleep_range[0] + sleep_range[1]) / 2.0)

    return all_items


############################################################
# 8. 메인 실행부 (구 하나 끝날 때마다 CSV append)
############################################################

def main():
    parser = argparse.ArgumentParser(description="중고나라 서울 25개구 × 카테고리 크롤러")
    parser.add_argument(
        "--category",
        type=str,
        default="all",
        help="크롤링할 카테고리명 (아이폰, 아이패드, 맥북, 애플워치, 에어팟, all)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="items_raw_seoul_joongna.csv",
        help="출력 CSV 파일명",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=20,
        help="구×카테고리 조합당 최대 페이지 수(기본 20)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="브라우저 창 띄우기",
    )

    args = parser.parse_args()

    # 카테고리 선택
    if args.category == "all":
        target_categories = list(CATEGORY_MAP.items())  # (이름, id)
    else:
        if args.category not in CATEGORY_MAP:
            raise ValueError(f"알 수 없는 카테고리명: {args.category}")
        target_categories = [(args.category, CATEGORY_MAP[args.category])]

    # CSV 헤더를 먼저 한 번만 써두기 (매 실행마다 새로 생성)
    fieldnames = [
        "source",
        "external_id",
        "category_id",
        "title",
        "price",
        "url",
        "status",
        "sd",
        "sgg",
        "emd",
        "posted_at",
        "posted_updated_at",
        "last_crawled_at",
    ]
    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
    print(f"📄 새 CSV 생성 및 헤더 작성: {args.output}")

    driver = create_driver(headless=not args.no_headless)
    total_count = 0  # 전체 누적 개수

    try:
        for category_name, category_id in target_categories:
            print("\n" + "=" * 60)
            print(f"▶ 카테고리: {category_name} (id={category_id}) 크롤링 시작")
            print("=" * 60)

            for gu in SEOUL_DISTRICTS:
                keyword = f"{gu} {category_name}"
                print(
                    f"\n------------------------------\n"
                    f" [키워드] {keyword}\n"
                    f"------------------------------"
                )

                items = crawl_keyword(
                    driver=driver,
                    keyword=keyword,
                    category_id=category_id,
                    sd_hint="서울특별시",
                    sgg_hint=gu,
                    max_pages=args.max_pages,
                )

                print(f"  → {keyword} 에서 최종 {len(items)}개 수집")

                # ✅ 여기서 바로 CSV에 append
                if items:
                    with open(args.output, "a", newline="", encoding="utf-8-sig") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        for row in items:
                            writer.writerow(row)
                    total_count += len(items)
                    print(f"  📁 {args.output} 에 {len(items)}개 행 추가 (누적 {total_count}개)")
                else:
                    print("  ⚠️ 저장할 아이템이 없습니다 (이 키워드 스킵).")

        print("\n" + "=" * 60)
        print(f"✅ 전체 크롤링 완료. 최종 누적 row 수: {total_count}")
        print(f"✅ 결과 파일: {args.output}")
        print("=" * 60)

    finally:
        try:
            driver.quit()
        except Exception:
            pass
        print("🔒 WebDriver 종료")


if __name__ == "__main__":
    main()
