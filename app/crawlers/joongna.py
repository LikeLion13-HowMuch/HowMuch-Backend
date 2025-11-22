# -*- coding: utf-8 -*-
"""
중고나라 검색 기반 크롤링 (Selenium 버전)
- Selenium을 사용하여 JavaScript 렌더링 후 HTML 파싱
- 검색 결과 페이지에서 상품 카드(li)별로
  링크 / 위치 / 시간 수집
- 각 상품 상세 페이지를 파싱해서 name / price 추출
- 최종적으로 name / price / location / time / link 를 CSV로 저장
"""

import re
import os
import json
import time
import random
import argparse
from typing import Optional, List, Dict, Tuple
from urllib.parse import quote, urljoin

import requests
import pandas as pd
from bs4 import BeautifulSoup

# Selenium imports
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# ===== 설정 =====

BASE_SEARCH_URL = "https://web.joongna.com/search/{keyword}?keywordSource=INPUT_KEYWORD"
# 중고나라 API 엔드포인트
API_SEARCH_URL = "https://api.joongna.com/v3/search/products"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://web.joongna.com/",
}

API_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Origin": "https://web.joongna.com",
    "Referer": "https://web.joongna.com/",
}

PRICE_PAT = re.compile(r"(\d{1,3}(?:,\d{3})*|\d+)\s*원")
# 숫자(영문/전각 모두) + 단위 + "전"
TIME_PAT = re.compile(r"[0-9０-９]+\s*(초|분|시간|일|주|개월|달)\s*전")


# ================== Selenium WebDriver 설정 ==================


def create_driver(headless: bool = True) -> webdriver.Chrome:
    """
    Chrome WebDriver 생성
    - headless: True면 브라우저 창을 띄우지 않음
    """
    chrome_options = Options()

    if headless:
        chrome_options.add_argument("--headless")

    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument(f"user-agent={USER_AGENT}")
    chrome_options.add_argument("--window-size=1920,1080")

    # 이미지 로딩 비활성화로 속도 향상
    prefs = {"profile.managed_default_content_settings.images": 2}
    chrome_options.add_experimental_option("prefs", prefs)

    driver = webdriver.Chrome(options=chrome_options)
    driver.implicitly_wait(10)

    return driver


# ================== JSON-LD / 가격 파서 ==================


def _parse_jsonld_product(soup: BeautifulSoup) -> dict:
    """JSON-LD(Product)의 name/price/seller/date* 등을 추출."""
    out = {"name": None, "price": None, "seller": None, "date": None}
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or ""
        try:
            data = json.loads(raw)
        except Exception:
            continue

        items = data if isinstance(data, list) else [data]
        for obj in items:
            if not isinstance(obj, dict):
                continue

            # datePublished/Modified/uploadDate/releaseDate 중 택1
            for date_key in ("datePublished", "dateModified", "uploadDate", "releaseDate"):
                if obj.get(date_key):
                    out["date"] = str(obj[date_key])
                    break

            if obj.get("@type") == "Product":
                out["name"] = out["name"] or obj.get("name")
                offers = obj.get("offers")
                price = None
                seller = None
                if isinstance(offers, dict):
                    price = offers.get("price")
                    seller = (offers.get("seller") or {}).get("name")
                elif isinstance(offers, list) and offers:
                    price = offers[0].get("price")
                    seller = (offers[0].get("seller") or {}).get("name")

                try:
                    price = int(str(price).replace(",", "")) if price is not None else None
                except Exception:
                    price = None

                out["price"] = price if out["price"] is None else out["price"]
                out["seller"] = seller if out["seller"] is None else out["seller"]

    return out


def _extract_price_from_text(soup: BeautifulSoup) -> Optional[int]:
    """여러 후보 노드와 전체 텍스트에서 가격 정규식으로 백업 추출."""
    candidates: List[str] = []
    candidates += [
        el.get_text(" ", strip=True)
        for el in soup.select(
            "div[class*='price'], span[class*='price'], div.font-semibold"
        )
    ]
    candidates.append(soup.get_text(" ", strip=True)[:8000])

    for txt in candidates:
        txt = (txt or "").replace("\u00a0", " ")
        m = PRICE_PAT.search(txt)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except Exception:
                pass
    return None


# ================== 시간/위치 판별 유틸 ==================


def looks_like_time(text: str) -> bool:
    """'32분 전' 같은 시간 문자열인지 대충 판단."""
    text = text.replace("\u00a0", " ").strip()
    if "전" not in text:
        return False
    if not TIME_PAT.search(text):
        return False
    return True


def looks_like_location(text: str) -> bool:
    """'인계동', '논현1동' 같은 위치 문자열인지 대충 판단."""
    text = text.replace("\u00a0", " ").strip()

    if not text:
        return False
    if "|" in text:
        return False
    if "원" in text:
        return False
    if looks_like_time(text):
        return False
    # 너무 길면 제목일 확률 높음
    if len(text) > 15:
        return False

    # 동/구/시/읍/면/리 같은 지명 접미사 포함하면 위치일 확률 높음
    if any(suffix in text for suffix in ["동", "구", "시", "읍", "면", "리"]):
        return True

    return False


# ================== 상세 페이지 파서 (이름/가격만) ==================


def parse_product_page(url: str, save_html: bool = False) -> Optional[dict]:
    """상품 상세페이지에서 name/price/time/location 추출"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 디버그: 첫 상세 페이지 HTML 저장
        if save_html:
            with open("first_product_detail.html", "w", encoding="utf-8") as f:
                f.write(resp.text)
            print(f"  💾 상세 페이지 HTML 저장됨: first_product_detail.html")

        # JSON-LD(Product) 우선
        jl = _parse_jsonld_product(soup)
        name = jl.get("name")
        price = jl.get("price")

        # 텍스트 백업 (상품명/가격)
        if not name:
            h1 = soup.select_one("h1")
            name = h1.get_text(strip=True) if h1 else "상품명없음"
        if price is None:
            p2 = _extract_price_from_text(soup)
            price = int(p2) if p2 is not None else 0

        # 시간과 위치 정보 추출 시도
        time_val = "시간없음"
        location = "지역없음"

        # 전체 텍스트에서 시간 정보 찾기
        full_text = soup.get_text(" ", strip=True)
        time_match = TIME_PAT.search(full_text)
        if time_match:
            time_val = time_match.group(0)

        # span.text-gray-400 같은 요소들에서 위치/시간 찾기
        gray_spans = soup.select("span.text-gray-400, span.text-sm")
        for s in gray_spans:
            txt = s.get_text(strip=True)
            if not txt or txt == "|":
                continue

            if looks_like_time(txt):
                time_val = txt
            elif looks_like_location(txt) and location == "지역없음":
                location = txt

        # 디버그 출력
        if save_html:
            print(f"\n  [상세페이지 디버그]")
            print(f"    시간: {time_val}")
            print(f"    위치: {location}")
            print(f"    전체 텍스트 앞 500자: {full_text[:500]}")
            print(f"    gray_spans 개수: {len(gray_spans)}")

        return {
            "name": name or "상품명없음",
            "price": int(price)
            if isinstance(price, (int, float, str)) and str(price).isdigit()
            else (price or 0),
            "time": time_val,
            "location": location,
        }

    except Exception as e:
        print(f"❌ {url} 파싱 실패: {e}")
        return None


# ================== API 기반 검색 ==================


def fetch_search_api(keyword: str, page: int = 0) -> Optional[dict]:
    """
    중고나라 API를 사용하여 검색 결과 가져오기
    - page는 0부터 시작
    """
    params = {
        "keyword": keyword,
        "page": page,
        "pageSize": 40,  # 한 페이지당 상품 개수
        "sort": "RECENT",  # RECENT, LOW_PRICE, HIGH_PRICE, POPULAR
    }

    print(f"🌐 API 검색 요청: {API_SEARCH_URL} (page={page})")
    try:
        resp = requests.get(API_SEARCH_URL, headers=API_HEADERS, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return data
    except Exception as e:
        print(f"❌ API 요청 실패 (page={page}): {e}")
        return None


def extract_products_from_api(data: dict, debug: bool = False) -> List[Dict[str, str]]:
    """
    API 응답에서 상품 정보 추출
    """
    products: List[Dict[str, str]] = []

    if not data or "data" not in data:
        return products

    items = data.get("data", {}).get("items", [])

    for idx, item in enumerate(items):
        product_id = item.get("productSeq") or item.get("seq") or item.get("id")
        if not product_id:
            continue

        name = item.get("title") or item.get("productTitle") or "상품명없음"
        price = item.get("price", 0)
        location = item.get("town") or item.get("location") or "지역없음"

        # 시간 정보 추출
        time_val = "시간없음"
        # createdAt, updatedAt, publishedAt 등의 필드가 있을 수 있음
        for time_field in ["timeAgo", "time", "createdAt", "updatedAt", "publishedAt"]:
            if item.get(time_field):
                time_val = str(item[time_field])
                break

        link = f"https://web.joongna.com/product/{product_id}"

        # 디버깅 모드: 처음 3개 상품의 원본 데이터 출력
        if debug and idx < 3:
            print(f"\n[DEBUG API] 상품 #{idx + 1}")
            print(f"  원본 데이터 키: {list(item.keys())}")
            print(f"  이름: {name}")
            print(f"  가격: {price}")
            print(f"  위치: {location}")
            print(f"  시간: {time_val}")
            print(f"  링크: {link}")

        products.append({
            "name": name,
            "price": int(price) if isinstance(price, (int, float)) else 0,
            "location": location,
            "time": time_val,
            "link": link,
        })

    print(f"   └─ API에서 상품 {len(products)}개 추출")
    return products


# ================== Selenium 기반 검색 ==================


def fetch_search_page_selenium(driver: webdriver.Chrome, keyword: str, page: int = 1, save_html: bool = False) -> Optional[str]:
    """
    Selenium을 사용하여 검색 결과 페이지 가져오기
    - JavaScript 렌더링 완료 후 HTML 반환
    """
    encoded_keyword = quote(keyword)
    url = BASE_SEARCH_URL.format(keyword=encoded_keyword)

    if page > 1:
        url = f"{url}&page={page}"

    print(f"🌐 검색 페이지 요청 (Selenium): {url}")

    try:
        driver.get(url)

        # 상품 리스트가 로딩될 때까지 대기
        wait = WebDriverWait(driver, 10)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "ul.grid li")))

        # JavaScript 실행 완료를 위해 추가 대기
        time.sleep(2)

        html = driver.page_source

        # 디버그: HTML 저장
        if save_html and page == 1:
            with open(f"{keyword}_search_page_selenium.html", "w", encoding="utf-8") as f:
                f.write(html)
            print(f"  💾 HTML 저장됨: {keyword}_search_page_selenium.html")

        return html

    except TimeoutException:
        print(f"❌ 검색 페이지 로딩 타임아웃 (page={page})")
        return None
    except Exception as e:
        print(f"❌ 검색 페이지 요청 실패 (page={page}): {e}")
        return None


# ================== 검색 페이지 HTML 파서 (requests 버전) ==================


def fetch_search_page_html(keyword: str, page: int = 1, save_html: bool = False) -> Optional[str]:
    """
    검색 결과 페이지 HTML 요청
    - 1페이지: /search/키워드?keywordSource=INPUT_KEYWORD
    - 2페이지~: /search/키워드?keywordSource=INPUT_KEYWORD&page=2
    """
    encoded_keyword = quote(keyword)  # "아이폰" -> "%EC%95%84%EC%9D%B4%ED%8F%B0"
    url = BASE_SEARCH_URL.format(keyword=encoded_keyword)

    # 2페이지 이상일 때만 &page= 붙이기
    if page > 1:
        url = f"{url}&page={page}"

    print(f"🌐 검색 페이지 요청: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()

        # 디버그: HTML 저장
        if save_html and page == 1:
            with open(f"{keyword}_search_page.html", "w", encoding="utf-8") as f:
                f.write(resp.text)
            print(f"  💾 HTML 저장됨: {keyword}_search_page.html")

        return resp.text
    except Exception as e:
        print(f"❌ 검색 페이지 요청 실패 (page={page}): {e}")
        return None


def _extract_location_time_from_li(li: BeautifulSoup) -> Tuple[str, str]:
    """
    li 카드 하나에서 위치 / 시간 텍스트 추출.
    - div.mt-1.mb-2 안의 span들에서 위치/시간 정보를 찾는다.
    """
    location = "지역없음"
    time_val = "시간없음"

    # 방법 1: 특정 div(mt-1 mb-2) 안의 span들 찾기
    # <div class="mt-1 mb-2 min-h-6 max-lg:mb-0 max-lg:mt-1.5">
    info_div = li.select_one("div.mt-1.mb-2, div[class*='mt-1'][class*='mb-2']")
    if info_div:
        spans = info_div.find_all("span")
        for s in spans:
            txt = s.get_text(strip=True)
            if not txt or txt == "|":
                continue

            # 시간 패턴 체크 (우선순위)
            if looks_like_time(txt):
                time_val = txt
            # 위치 패턴 체크
            elif looks_like_location(txt) and location == "지역없음":
                location = txt

    # 방법 2: 모든 span에서 text-gray-400 클래스를 가진 것들 찾기
    if location == "지역없음" or time_val == "시간없음":
        gray_spans = li.select("span.text-gray-400")
        for s in gray_spans:
            txt = s.get_text(strip=True)
            if not txt or txt == "|":
                continue

            if time_val == "시간없음" and looks_like_time(txt):
                time_val = txt
            elif location == "지역없음" and looks_like_location(txt):
                location = txt

    # 방법 3: 모든 span 검색
    if location == "지역없음" or time_val == "시간없음":
        span_texts: List[str] = []
        for s in li.find_all("span"):
            txt = s.get_text(strip=True)
            if not txt or txt == "|":
                continue
            span_texts.append(txt)

        if time_val == "시간없음":
            time_candidates = [t for t in span_texts if looks_like_time(t)]
            if time_candidates:
                # 여러 개면 제일 마지막을 시간으로
                time_val = time_candidates[-1]

        if location == "지역없음":
            loc_candidates = [t for t in span_texts if looks_like_location(t)]
            if loc_candidates:
                location = loc_candidates[0]

    # 방법 4: 그래도 시간 못 찾았으면 li 전체 텍스트에서 정규식으로 탐색
    if time_val == "시간없음":
        full_txt = li.get_text(" ", strip=True)
        match = TIME_PAT.search(full_txt)
        if match:
            time_val = match.group(0)

    return location, time_val


def extract_products_from_search(html: str, debug: bool = False) -> List[Dict[str, str]]:
    """
    검색 결과 HTML에서 상품 카드(li)별로
    - link
    - location
    - time
    을 추출.
    """
    soup = BeautifulSoup(html, "html.parser")
    products: List[Dict[str, str]] = []
    seen: set[str] = set()

    # grid 안의 li가 각각 카드
    for idx, li in enumerate(soup.select("ul.grid li")):
        a = li.select_one("a[href*='/product/']")
        if not a:
            continue

        href = a.get("href", "")
        if not href:
            continue

        # 상품이 아닌 등록 페이지 등은 제외
        if "/product/form" in href:
            continue

        full_url = urljoin("https://web.joongna.com", href)
        if full_url in seen:
            continue
        seen.add(full_url)

        location, time_val = _extract_location_time_from_li(li)

        # 디버깅 모드: 처음 3개 상품의 HTML 구조 출력
        if debug and idx < 3:
            print(f"\n[DEBUG] 상품 #{idx + 1}")
            print(f"  위치: {location}")
            print(f"  시간: {time_val}")
            print(f"  링크: {full_url}")

            # 첫 번째 상품의 전체 HTML 저장
            if idx == 0:
                with open(f"first_product_card.html", "w", encoding="utf-8") as f:
                    f.write(li.prettify())
                print(f"  💾 첫 상품 카드 HTML 저장됨: first_product_card.html")

            # div.mt-1.mb-2 찾기
            info_div = li.select_one("div.mt-1.mb-2, div[class*='mt-1'][class*='mb-2']")
            if info_div:
                print(f"  info_div 텍스트: {info_div.get_text(' ', strip=True)}")
                print(f"  info_div HTML: {info_div}")
                print(f"  info_div span 개수: {len(info_div.find_all('span'))}")
            else:
                print(f"  info_div를 찾지 못함")

            # 모든 span 출력
            all_spans = li.find_all("span")
            print(f"  전체 span 개수: {len(all_spans)}")
            for i, s in enumerate(all_spans[:10]):  # 처음 10개만
                txt = s.get_text(strip=True)
                classes = s.get("class", [])
                if txt:
                    print(f"    span[{i}]: '{txt}' | classes: {classes}")

        products.append(
            {
                "link": full_url,
                "location": location,
                "time": time_val,
            }
        )

    print(f"   └─ 검색 페이지에서 상품 카드 {len(products)}개 추출")
    return products


# ================== 오케스트레이션(API 버전) ==================


def crawl_search_api(keyword: str, limit: int = 200, sleep_range=(1.0, 3.0), debug: bool = False) -> List[dict]:
    """
    중고나라 API를 사용한 상품 크롤링
    - keyword: 검색할 키워드 (예: "아이폰")
    - limit: 수집할 상품 개수
    - debug: 디버그 모드
    """
    results: List[dict] = []
    page = 0

    print(f"🔍 '{keyword}' 검색 결과 크롤링 시작 (API 버전)...\n")

    while len(results) < limit:
        print(f"📄 검색 결과 페이지 {page + 1} 요청 중...")

        data = fetch_search_api(keyword, page=page)
        if not data:
            print("⚠️ API 응답이 비어있습니다. 크롤링 종료.")
            break

        # 첫 페이지만 디버그 출력
        products = extract_products_from_api(data, debug=(debug and page == 0))
        if not products:
            print("⚠️ 더 이상 상품이 없습니다. 크롤링 종료.")
            break

        for product in products:
            if len(results) >= limit:
                break

            print(
                f"✅ {product['name']} / {product['price']}원 / "
                f"{product['location']} / {product['time']}"
            )

            results.append(product)
            time.sleep(random.uniform(*sleep_range))

        page += 1
        time.sleep(random.uniform(*sleep_range))

    return results


# ================== 오케스트레이션(Selenium 버전) ==================


def crawl_search_selenium(keyword: str, limit: int = 200, sleep_range=(1.0, 3.0), debug: bool = False, headless: bool = True) -> List[dict]:
    """
    Selenium을 사용한 상품 크롤링
    - keyword: 검색할 키워드 (예: "아이폰")
    - limit: 수집할 상품 개수
    - debug: 디버그 모드
    - headless: True면 브라우저 창을 띄우지 않음
    """
    results: List[dict] = []
    page = 1

    print(f"🔍 '{keyword}' 검색 결과 크롤링 시작 (Selenium 버전)...\n")

    # WebDriver 생성
    driver = create_driver(headless=headless)

    try:
        while len(results) < limit:
            print(f"📄 검색 결과 페이지 {page} 요청 중...")

            # 첫 페이지만 HTML 저장
            save_html = (page == 1 and globals().get('SAVE_HTML', False))
            html = fetch_search_page_selenium(driver, keyword, page=page, save_html=save_html)
            if not html:
                print("⚠️ 검색 페이지 응답이 비어있습니다. 크롤링 종료.")
                break

            # 첫 페이지만 디버그 출력
            product_cards = extract_products_from_search(html, debug=(debug and page == 1))
            if not product_cards:
                print("⚠️ 더 이상 상품 카드가 없습니다. 크롤링 종료.")
                break

            for card in product_cards:
                if len(results) >= limit:
                    break

                link = card["link"]
                location_from_search = card["location"]
                time_from_search = card["time"]

                # 첫 상품만 HTML 저장
                save_detail_html = (len(results) == 0 and globals().get('SAVE_HTML', False))
                detail = parse_product_page(link, save_html=save_detail_html)
                if not detail:
                    continue

                # 상세 페이지에서 가져온 정보 우선 사용, 없으면 검색 페이지 정보 사용
                location = detail.get("location", "지역없음")
                if location == "지역없음":
                    location = location_from_search

                time_val = detail.get("time", "시간없음")
                if time_val == "시간없음":
                    time_val = time_from_search

                row = {
                    "name": detail["name"],
                    "price": detail["price"],
                    "location": location,
                    "time": time_val,
                    "link": link,
                }

                print(
                    f"✅ {row['name']} / {row['price']}원 / "
                    f"{row['location']} / {row['time']}"
                )

                results.append(row)

                time.sleep(random.uniform(*sleep_range))  # 상세 페이지 크롤링 간 딜레이

            page += 1
            time.sleep(random.uniform(*sleep_range))  # 페이지 전환 딜레이

    finally:
        # WebDriver 종료
        driver.quit()
        print("\n🔒 WebDriver 종료")

    return results


# ================== 오케스트레이션(HTML 버전 - requests) ==================


def crawl_search_results(keyword: str, limit: int = 200, sleep_range=(1.0, 3.0), debug: bool = False) -> List[dict]:
    """
    검색 키워드 기반 상품 크롤링 (HTML 버전)
    - keyword: 검색할 키워드 (예: "아이폰")
    - limit: 수집할 상품 개수
    - debug: 디버그 모드 활성화 (처음 몇 개 상품의 상세 정보 출력)
    """
    results: List[dict] = []
    page = 1

    print(f"🔍 '{keyword}' 검색 결과 크롤링 시작...\n")

    while len(results) < limit:
        print(f"📄 검색 결과 페이지 {page} 요청 중...")

        # 첫 페이지만 HTML 저장
        save_html = (page == 1 and globals().get('SAVE_HTML', False))
        html = fetch_search_page_html(keyword, page=page, save_html=save_html)
        if not html:
            print("⚠️ 검색 페이지 응답이 비어있습니다. 크롤링 종료.")
            break

        # 첫 페이지만 디버그 출력
        product_cards = extract_products_from_search(html, debug=(debug and page == 1))
        if not product_cards:
            print("⚠️ 더 이상 상품 카드가 없습니다. 크롤링 종료.")
            break

        for card in product_cards:
            if len(results) >= limit:
                break

            link = card["link"]
            location_from_search = card["location"]
            time_from_search = card["time"]

            # 첫 상품만 HTML 저장
            save_detail_html = (len(results) == 0 and globals().get('SAVE_HTML', False))
            detail = parse_product_page(link, save_html=save_detail_html)
            if not detail:
                continue

            # 상세 페이지에서 가져온 정보 우선 사용, 없으면 검색 페이지 정보 사용
            location = detail.get("location", "지역없음")
            if location == "지역없음":
                location = location_from_search

            time_val = detail.get("time", "시간없음")
            if time_val == "시간없음":
                time_val = time_from_search

            row = {
                "name": detail["name"],
                "price": detail["price"],
                "location": location,
                "time": time_val,
                "link": link,
            }

            print(
                f"✅ {row['name']} / {row['price']}원 / "
                f"{row['location']} / {row['time']}"
            )

            results.append(row)

            time.sleep(random.uniform(*sleep_range))  # 상세 페이지 크롤링 간 딜레이

        page += 1
        time.sleep(random.uniform(*sleep_range))  # 페이지 전환 딜레이

    return results


# ================== 실행 진입점 ==================

if __name__ == "__main__":
    # 커맨드 라인 인자 파싱
    parser = argparse.ArgumentParser(description="중고나라 크롤러")
    parser.add_argument("-k", "--keyword", type=str, default="아이폰", help="검색 키워드 (기본값: 아이폰)")
    parser.add_argument("-l", "--limit", type=int, default=50, help="수집할 상품 개수 (기본값: 50)")
    parser.add_argument("-d", "--debug", action="store_true", help="디버그 모드 활성화")
    parser.add_argument("--no-headless", action="store_true", help="브라우저 창 표시")
    parser.add_argument("--save-html", action="store_true", help="HTML 파일 저장")
    parser.add_argument("--no-selenium", action="store_true", help="requests 사용 (Selenium 비활성화)")
    parser.add_argument("--save-db", action="store_true", help="데이터베이스에 저장")
    parser.add_argument("--category", type=str, default="iPhone", help="카테고리명 (기본값: iPhone)")
    parser.add_argument("--no-csv", action="store_true", help="CSV 파일 저장 안 함")

    args = parser.parse_args()

    KEYWORD = args.keyword
    LIMIT = args.limit
    DEBUG = args.debug
    USE_SELENIUM = not args.no_selenium
    HEADLESS = not args.no_headless
    SAVE_HTML = args.save_html

    print("=" * 60)
    if USE_SELENIUM:
        print(f"중고나라 '{KEYWORD}' 검색 크롤링 (Selenium 버전)")
    else:
        print(f"중고나라 '{KEYWORD}' 검색 크롤링 (requests 버전)")
    print(f"수집 개수: {LIMIT}개")
    print("=" * 60 + "\n")

    if USE_SELENIUM:
        data = crawl_search_selenium(
            keyword=KEYWORD,
            limit=LIMIT,
            sleep_range=(0.5, 1.5),
            debug=DEBUG,
            headless=HEADLESS
        )
    else:
        data = crawl_search_results(keyword=KEYWORD, limit=LIMIT, sleep_range=(0.5, 1.5), debug=DEBUG)

    if not data:
        print(f"\n'{KEYWORD}' 검색 결과가 없습니다.")
        raise SystemExit(0)

    print("\n" + "=" * 60)
    print(f"✨ 총 {len(data)}개의 '{KEYWORD}' 상품을 수집했습니다.")
    print("=" * 60 + "\n")

    # CSV 저장
    if not args.no_csv:
        df = pd.DataFrame(data)
        if USE_SELENIUM:
            out_csv = f"{KEYWORD}_products_selenium.csv"
            version_text = "Selenium 버전"
        else:
            out_csv = f"{KEYWORD}_products_requests.csv"
            version_text = "requests 버전"

        df.to_csv(out_csv, encoding="utf-8-sig", index=False)
        print(f"📁 CSV 저장 완료: {os.path.abspath(out_csv)} ({version_text})")

    # 데이터베이스 저장
    if args.save_db:
        try:
            from db_manager import DatabaseManager

            print("\n" + "=" * 60)
            print("💾 데이터베이스에 저장 중...")
            print("=" * 60 + "\n")

            db = DatabaseManager()
            success_count = db.insert_items_batch(
                products=data,
                marketplace_code="joongna",
                category_name=args.category
            )
            db.close()

            print("\n" + "=" * 60)
            print(f"✅ 데이터베이스 저장 완료: {success_count}/{len(data)}개")
            print("=" * 60)

        except ImportError:
            print("❌ db_manager.py를 찾을 수 없습니다.")
        except Exception as e:
            print(f"❌ 데이터베이스 저장 실패: {e}")

    print("\n🎉 크롤링 완료!")
