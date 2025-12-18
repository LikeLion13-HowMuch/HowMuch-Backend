# -*- coding: utf-8 -*-
"""
번개장터 크롤러 (체크포인트 + 안전저장 + 부속품 필터 + 절대값 가드 + 선택적 증분 수집)

기능 요약
- CATEGORY_MAP(아이폰/아이패드/맥북/워치/에어팟) 모든 키워드 검색
- 표준 CSV 스키마로 저장(행 단위 flush) + checkpoint.json로 이어받기
- 카테고리별 부속품(케이스/필름/밴드/허브 등) 제목 필터
- 카테고리별 가격 절대값 가드 (초기 데이터 수집 안정화)
- (선택) baseline 또는 러닝 평균 기반 외도 필터
- 번개장터 검색 API 직접 호출 (Playwright 불필요)
- '끌어올리기'에 대응하는 '인내심' 기반 증분 수집 로직 적용
"""
import asyncio, csv, json, os, random, re, signal, httpx
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Optional, Set
from urllib.parse import quote

# --- 설정 ---
CATEGORY_MAP: Dict[str, int] = {"아이폰": 1, "아이패드": 2, "맥북": 3, "애플워치": 4, "에어팟": 5}
IPHONE, IPAD, MACBOOK, WATCH, AIRPODS = (CATEGORY_MAP["아이폰"], CATEGORY_MAP["아이패드"],
                                         CATEGORY_MAP["맥북"], CATEGORY_MAP["애플워치"], CATEGORY_MAP["에어팟"])
BUNJANG_DIGITAL_CATEGORY_ID = "600"

CONCURRENCY = 8  # API 호출 동시성
MAX_PAGES_PER_QUERY = 100  # 키워드당 최대 탐색 페이지 수 (증분 수집 시에도 전체 탐색 방지용)
ITEMS_PER_PAGE = 100 # 페이지 당 아이템 수 (번개장터 API 최대치 근접)
CONSECUTIVE_SEEN_THRESHOLD = 30 # 연속해서 이미 본 상품을 몇 개 만나면 중단할지 (끌어올리기 대응)

OUTPUT_CSV = "bunjang_items_raw.csv"
CHECKPOINT = "bunjang_checkpoint.json"
BASELINE_JSON = "bunjang_price_baseline.json"

ENABLE_TITLE_FILTER = True
ENABLE_PRICE_GUARD = True
ENABLE_PRICE_FILTER = False

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
API_BASE_URL = "https://api.bunjang.co.kr/api/1/find_v2.json"

CSV_COLS = ["source", "external_id", "category_id", "title", "price", "url", "status", "sd", "sgg", "emd",
            "posted_at", "posted_updated_at", "last_crawled_at"]

# --- 전역 변수 ---
csv_lock = asyncio.Lock()
checkpoint_lock = asyncio.Lock()
stop_flag = False
running_stats: Dict[int, Dict[str, float]] = {}
baselines: Dict[str, Dict[str, float]] = {}
seen_pids: Set[str] = set() # 증분 수집을 위한, 이미 CSV에 저장된 PID 목록

# --- 가격/제목 필터링 로직 (기존 코드 재사용) ---
CATEGORY_PRICE_GUARD = {IPHONE: {"min": 30000, "max": 5000000}, IPAD: {"min": 30000, "max": 4000000},
                        MACBOOK: {"min": 100000, "max": 8000000}, WATCH: {"min": 20000, "max": 2000000},
                        AIRPODS: {"min": 10000, "max": 800000}}

def price_is_ridiculous(category_id: int, price: Optional[int]) -> bool:
    if price is None: return True
    g = CATEGORY_PRICE_GUARD.get(category_id)
    return False if not g else not (g["min"] <= price <= g["max"])

def _norm(s: str) -> str: return re.sub(r"\s+", "", (s or "")).lower()
def _contains_any(text: str, kws: List[str]) -> bool:
    t = _norm(text); return any(_norm(k) in t for k in kws)

ACCESSORY_CORE = {
    IPHONE: ["케이스", "필름", "보호필름", "강화유리", "충전기", "어댑터", "케이블"],
    IPAD: ["케이스", "커버", "폴리오", "키보드", "펜슬팁", "필름", "충전기"],
    MACBOOK: ["파우치", "슬리브", "케이스", "키스킨", "필름", "독", "허브", "어댑터", "충전기"],
    WATCH: ["밴드", "스트랩", "케이스", "범퍼", "보호필름", "충전기", "충전독"],
    AIRPODS: ["케이스", "키링", "이어팁", "폼팁", "스트랩", "충전기", "충전케이스"],
}
DEVICE_STRONG_HINTS = {
    IPHONE: ["본체", "풀박스", "자급제", "미개봉", "리퍼", "공기계", "배터리성능", "128gb", "256gb", "512gb", "1tb"],
    IPAD: ["본체", "풀박스", "자급제", "미개봉", "wifi", "cellular", "11형", "12.9"],
    MACBOOK: ["본체", "풀박스", "m1", "m2", "m3", "intel", "ram", "ssd", "13인치", "14인치", "16인치"],
    WATCH: ["본체", "풀박스", "울트라", "se", "gps", "cellular", "41mm", "45mm"],
    AIRPODS: ["본체", "충전케이스 포함", "미개봉", "정품"],
}
ACCESSORY_ONLY_HINTS = ["전용", "호환", "for", "용", "단품", "케이스만", "필름만", "스트랩만", "밴드만"]
INCLUSION_PHRASES = ["케이스 포함", "필름 부착", "사은품", "덤으로", "증정"]
BUYING_HINTS = ["삽니다", "구매합니다", "구해요", "찾습니다", "매입", "고가매입"]
SERVICE_HINTS = ["수리", "교체", "액정수리", "배터리교체", "위탁판매", "대여", "렌탈"]

def is_buying_or_service_title(title: str) -> bool:
    if not title: return False
    t = _norm(title)
    return any(k in t for k in BUYING_HINTS) or any(k in t for k in SERVICE_HINTS)

def is_accessory_title(title: str, category_id: int, price: Optional[int] = None, baseline_mean: Optional[float] = None) -> bool:
    if not ENABLE_TITLE_FILTER or not title: return False
    t = _norm(title)
    if not _contains_any(t, ACCESSORY_CORE.get(category_id, [])): return False
    if _contains_any(t, INCLUSION_PHRASES): return False
    if _contains_any(t, ACCESSORY_ONLY_HINTS): return True
    if _contains_any(t, DEVICE_STRONG_HINTS.get(category_id, [])): return False
    if price is not None and baseline_mean and price < max(50_000, baseline_mean * 0.25): return True
    return True

OUTLIER_RATIO = 0.50
MIN_SAMPLES_FOR_RUNNING = 20
def load_baselines():
    global baselines
    if os.path.exists(BASELINE_JSON):
        try:
            with open(BASELINE_JSON, "r", encoding="utf-8") as f:
                baselines = json.load(f) or {}
        except Exception:
            baselines = {}
    else:
        baselines = {}

def update_running_mean(category_id: int, price: int):
    st = running_stats.setdefault(category_id, {"n": 0.0, "mean": 0.0})
    n = st["n"]
    m = st["mean"]
    n2 = n + 1.0
    st["n"] = n2
    st["mean"] = m + (price - m) / n2 if n2 > 0 else price

def get_baseline_mean(category_id: int) -> Optional[float]:
    m = baselines.get(str(category_id), {}).get("mean")
    if isinstance(m, (int, float)): return float(m)
    st = running_stats.get(category_id)
    if st and st.get("n", 0) >= MIN_SAMPLES_FOR_RUNNING:
        return float(st["mean"])
    return None

def price_is_outlier(category_id: int, price: Optional[int]) -> bool:
    if not ENABLE_PRICE_FILTER or price is None: return False
    base = get_baseline_mean(category_id)
    if base is None or base <= 0: return False
    lo, hi = base * (1.0 - OUTLIER_RATIO), base * (1.0 + OUTLIER_RATIO)
    return not (lo <= price <= hi)

# --- 파일 I/O ---
def ensure_csv_header(path: str):
    need_header = (not os.path.exists(path)) or os.path.getsize(path) == 0
    if need_header:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(CSV_COLS)

async def append_row(row: dict):
    ensure_csv_header(OUTPUT_CSV)
    async with csv_lock:
        with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
            w.writerow(row)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass

def load_checkpoint() -> dict:
    if not os.path.exists(CHECKPOINT):
        return {}
    try:
        with open(CHECKPOINT, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}

async def save_checkpoint(data: dict):
    async with checkpoint_lock:
        tmp = CHECKPOINT + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp, CHECKPOINT)

def load_seen_pids() -> Set[str]:
    """이미 CSV에 저장된 상품 ID를 로드하여 Set으로 반환합니다."""
    _seen_pids = set()
    if not os.path.exists(OUTPUT_CSV):
        return _seen_pids
    
    try:
        with open(OUTPUT_CSV, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            # CSV_COLS에서 external_id의 인덱스를 찾아 사용
            # 헤더가 없거나 파일이 비어있으면 DictReader가 작동하지 않으므로 직접 처리
            if not reader.fieldnames:
                return _seen_pids
            
            for row in reader:
                if "external_id" in row and row["external_id"]:
                    _seen_pids.add(row["external_id"])
    except Exception as e:
        print(f"Error loading seen PIDs from CSV: {e}")
    return _seen_pids

# --- 데이터 파싱 및 변환 ---
def to_iso_utc_from_timestamp(ts: Optional[int]) -> Optional[str]:
    if ts is None: return None
    try:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None

def parse_price_int(price_text: str) -> Optional[int]:
    if not price_text or not isinstance(price_text, str): return None
    digits = re.sub(r"[^\d]", "", price_text)
    if digits == "": return None
    try:
        return int(digits)
    except ValueError:
        return None

def parse_location(loc_text: str) -> Tuple[str, str, str]:
    if not loc_text: return "", "", ""
    parts = loc_text.split()
    sd = parts[0] if len(parts) > 0 else ""
    sgg = parts[1] if len(parts) > 1 else ""
    emd = parts[2] if len(parts) > 2 else ""
    return sd, sgg, emd

# --- 핵심 크롤링 로직 ---
# process_item 함수는 이제 새로 발견된 아이템인지 여부를 True/False로 반환합니다.
async def process_item(item: Dict, category_id: int, _seen_pids: Set[str]) -> bool:
    external_id = item.get("pid")
    if not external_id: # ID가 없으면 처리 불가
        return False 

    # 이미 본 상품이면 저장하지 않고 False 반환
    if external_id in _seen_pids:
        return False
        
    if item.get("ad") or item.get("type") == "EXT_AD":
        return False
    
    title = item.get("name", "")
    price_val = parse_price_int(item.get("price", ""))
    
    # ---- 필터링 순서 ----
    if is_buying_or_service_title(title): return False
    if ENABLE_PRICE_GUARD and price_is_ridiculous(category_id, price_val): return False
    
    baseline_mean = get_baseline_mean(category_id) if ENABLE_PRICE_FILTER else None
    if is_accessory_title(title, category_id, price_val, baseline_mean): return False
    if price_is_outlier(category_id, price_val): return False

    if price_val is not None:
        update_running_mean(category_id, price_val)

    url = f"https://m.bunjang.co.kr/products/{external_id}" if external_id else ""
    
    sd, sgg, emd = parse_location(item.get("location", ""))

    # --- NEW FILTER: Only process items where sd is Seoul or a variation ---
    norm_sd = _norm(sd)
    # Check if the normalized sd is exactly "서울", "서울시", or "서울특별시"
    if norm_sd not in ["서울", "서울시", "서울특별시"]:
        return False
    # --- END NEW FILTER ---
    
    posted_at = to_iso_utc_from_timestamp(item.get("update_time"))
    
    row = {
        "source": "bunjang",
        "external_id": external_id, # external_id는 이제 None이 아님
        "category_id": category_id,
        "title": title,
        "price": price_val if price_val is not None else "",
        "url": url,
        "status": "active" if item.get("status") == "0" else item.get("status", ""),
        "sd": sd, "sgg": sgg, "emd": emd,
        "posted_at": posted_at or "",
        "posted_updated_at": "",
        "last_crawled_at": datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    }
    await append_row(row) 
    _seen_pids.add(external_id) # 새로 저장했으니 seen_pids에 추가
    return True # 새로운 아이템을 처리했음을 알림

# fetch_and_process_page 함수는 새로 발견된 아이템 수를 반환하며, 중단 신호도 보낼 수 있습니다.
async def fetch_and_process_page(session: httpx.AsyncClient, query: str, category_id: int, page_num: int, semaphore: asyncio.Semaphore, _seen_pids: Set[str]) -> Tuple[int, bool]:
    global stop_flag
    if stop_flag: return 0, False # (처리된 아이템 수, 중단 필요 여부)

    params = {
        "q": query,
        "order": "date",
        "page": page_num,
        "n": ITEMS_PER_PAGE,
        "f_category_id": BUNJANG_DIGITAL_CATEGORY_ID,
        "stat_device": "w",
        "version": "5",
        "req_ref": "search",
    }
    
    newly_processed_on_page_count = 0
    current_consecutive_seen_count = 0 # 이 변수를 페이지 단위로 초기화
    
    async with semaphore:
        try:
            resp = await session.get(API_BASE_URL, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()

            if data.get("result") != "success" or not data.get("list"):
                print(f"  [{query}] Page {page_num}: No items found or API error, or no list.")
                return 0, True # API 에러 또는 목록 없음, 중단 신호

            items = data["list"]
            if not items:
                print(f"  [{query}] Page {page_num}: No items on this page.")
                return 0, True # 빈 페이지, 중단 신호

            # 이 페이지의 아이템들을 처리하면서 newly_processed_on_page_count와 current_consecutive_seen_count를 계산
            for item in items: # gather를 사용하지 않고 순차적으로 처리하여 consecutive_seen_count를 정확히 계산
                is_new_item = await process_item(item, category_id, _seen_pids)
                if is_new_item: # 새로운 아이템을 발견
                    newly_processed_on_page_count += 1
                    current_consecutive_seen_count = 0 # 새로운 아이템을 만났으므로 카운트 리셋
                else: # 이미 본 아이템을 발견 (혹은 필터링된 아이템)
                    external_id = item.get("pid")
                    if external_id and external_id in _seen_pids: # 이미 본 아이템인 경우만 카운트 증가
                        current_consecutive_seen_count += 1
                    # 필터링된 아이템은 consecutive_seen_count에 영향을 주지 않음

            print(f"  [{query}] Page {page_num}: Processed {newly_processed_on_page_count} new items, Consecutive seen: {current_consecutive_seen_count}")
            
            # 연속으로 이미 본 아이템이 임계값을 넘으면 중단
            if current_consecutive_seen_count >= CONSECUTIVE_SEEN_THRESHOLD:
                print(f"  [{query}] Page {page_num}: Consecutive seen count {current_consecutive_seen_count} >= threshold {CONSECUTIVE_SEEN_THRESHOLD}. Stopping for this query.")
                return newly_processed_on_page_count, True # 중단 신호
            
            # 페이지의 아이템 수가 ITEM_PER_PAGE보다 적으면 마지막 페이지로 간주
            if len(items) < ITEMS_PER_PAGE:
                print(f"  [{query}] Page {page_num}: Less than {ITEMS_PER_PAGE} items, assuming last page. Stopping.")
                return newly_processed_on_page_count, True # 중단 신호

        except httpx.HTTPStatusError as e:
            print(f"  [{query}] Page {page_num}: HTTP Error {e.response.status_code}")
            await asyncio.sleep(5) # 재시도를 위해 잠시 대기
            return 0, False # 에러 발생했으나, 다음 페이지 시도 가능성을 위해 중단하지 않음
        except (httpx.RequestError, json.JSONDecodeError) as e:
            print(f"  [{query}] Page {page_num}: Request/JSON Error: {e}")
            return 0, True # 심각한 오류, 중단 신호
        except Exception as e:
            print(f"  [{query}] Page {page_num}: Unexpected Error: {e}")
            return 0, True # 예측 불가능한 오류, 중단 신호

    await asyncio.sleep(0.2 + random.random() * 0.3)
    return newly_processed_on_page_count, False # (처리된 아이템 수, 중단 필요 없음)

# crawl_all_pages_for_query 함수도 반환값 변경에 맞춰 수정
async def crawl_all_pages_for_query(session: httpx.AsyncClient, query: str, category_id: int, checkpoint: dict, _seen_pids: Set[str]):
    global stop_flag
    if stop_flag: return

    # 증분 수집에서는 항상 0페이지부터 시작
    start_page = 0
    
    print(f"[{query}] Crawling starts from page {start_page} for incremental update...")

    semaphore = asyncio.Semaphore(CONCURRENCY)
    
    for page_num in range(start_page, MAX_PAGES_PER_QUERY):
        if stop_flag: break
        
        newly_processed_on_page_count, should_stop = await fetch_and_process_page(session, query, category_id, page_num, semaphore, _seen_pids)
        
        # 증분 수집 모드에서는 키워드별 last_done_page를 유지할 필요 없음
        # (매번 0페이지부터 시작하기 때문)
        # checkpoint 관련 로직은 필요에 따라 제거
        
        if should_stop: # 더 이상 아이템이 없거나 에러 발생 시, 또는 연속 중복 임계값 도달 시
            print(f"[{query}] Stopping crawl for this query at page {page_num}.")
            break
    
    # 증분 수집 모드에서는 키워드별 last_done_page를 유지할 필요 없음
    # (매번 0페이지부터 시작하기 때문)
    # await save_checkpoint(checkpoint) # 필요시 전체 체크포인트 저장

def install_signal_handlers():
    def handler(signum, frame):
        global stop_flag
        if not stop_flag:
            stop_flag = True
            print(f"\n[신호] {signum} 수신: 안전 종료 진행 중... (CSV/체크포인트 보존)")
    try:
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
    except Exception:
        pass

async def main():
    global seen_pids # 전역 변수임을 명시

    load_baselines()
    install_signal_handlers()
    # checkpoint = load_checkpoint() # 증분 수집 모드에서는 페이지별 체크포인트 불필요

    # 증분 수집을 위해 기존 PID 로드
    seen_pids = load_seen_pids()
    print(f"Loaded {len(seen_pids)} existing PIDs from {OUTPUT_CSV}.")

    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(headers=headers) as session:
        for query, category_id in CATEGORY_MAP.items():
            if stop_flag: break
            print(f"\n==== 키워드 시작: {query} (category_id={category_id}) ====")
            # crawl_all_pages_for_query에 seen_pids 전달
            await crawl_all_pages_for_query(session, query, category_id, {}, seen_pids) # 빈 딕셔너리로 checkpoint 전달
    
    print("\nCrawling finished.")

if __name__ == "__main__":
    asyncio.run(main())