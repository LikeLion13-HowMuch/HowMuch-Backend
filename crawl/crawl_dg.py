# -*- coding: utf-8 -*-
# (content trimmed in analysis for brevity in this message)
# The full integrated crawler code was too long for one cell previously.
# Rewriting the entire content now in this cell.
"""
당근마켓 크롤러 (체크포인트 + 안전저장 + 부속품 필터 + 절대값 가드 + 선택적 외도 필터)

기능 요약
- CATEGORY_MAP(아이폰/아이패드/맥북/워치/에어팟) 모든 키워드 검색
- 표준 CSV 스키마로 저장(행 단위 flush) + checkpoint.json로 이어받기
- 카테고리별 부속품(케이스/필름/밴드/허브 등) 제목 필터
- 카테고리별 가격 절대값 가드 (초기 데이터 수집 안정화)
- (선택) baseline 또는 러닝 평균 기반 외도 필터
"""
import asyncio, csv, json, os, random, re, signal
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Optional
from urllib.parse import urljoin, urlparse, parse_qs, unquote, quote
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

CATEGORY_MAP: Dict[str, int] = {"아이폰":1,"아이패드":2,"맥북":3,"애플워치":4,"에어팟":5}
IPHONE, IPAD, MACBOOK, WATCH, AIRPODS = (CATEGORY_MAP["아이폰"], CATEGORY_MAP["아이패드"],
                                            CATEGORY_MAP["맥북"], CATEGORY_MAP["애플워치"], CATEGORY_MAP["에어팟"])
CONCURRENCY=4; MAX_SCROLL_ROUNDS=1; SCROLL_PAUSE=(0.6,1.0); MAX_PAGES=30
OUTPUT_CSV="items_raw.csv"; CHECKPOINT="checkpoint.json"; BASELINE_JSON="price_baseline.json"
ENABLE_TITLE_FILTER=True; ENABLE_PRICE_GUARD=True; ENABLE_PRICE_FILTER=False
HEADLESS=True
USER_AGENT=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/140 Safari/537.36")
BASE="https://www.daangn.com"
LIST_LINK_SELECTOR='div[data-gtm="search_article"] a'
PRIORITY_SELECTORS=['a[data-gtm="search_article"]',"a[href*='/articles/']"]
TITLE_SELECTORS=["h1"]; PRICE_SELECTORS=["h3"]; TIME_SELECTORS=["time[datetime]","time"]
SEOUL_GU=["종로구","중구","용산구","성동구","광진구","동대문구","중랑구","성북구","강북구","도봉구","노원구",
          "은평구","서대문구","마포구","양천구","강서구","구로구","금천구","영등포구","동작구","관악구",
          "서초구","강남구","송파구","강동구"]
CSV_COLS=["source","external_id","category_id","title","price","url","status","sd","sgg","emd",
          "posted_at","posted_updated_at","last_crawled_at"]

csv_lock=asyncio.Lock(); checkpoint_lock=asyncio.Lock(); stop_flag=False
running_stats: Dict[int, Dict[str, float]]={}; baselines: Dict[str, Dict[str, float]]={}

CATEGORY_PRICE_GUARD={IPHONE:{"min":30000,"max":5000000}, IPAD:{"min":30000,"max":4000000},
                      MACBOOK:{"min":100000,"max":8000000}, WATCH:{"min":20000,"max":2000000},
                      AIRPODS:{"min":10000,"max":800000}}
def price_is_ridiculous(category_id:int, price:Optional[int])->bool:
    if price is None: return True
    g=CATEGORY_PRICE_GUARD.get(category_id); 
    return False if not g else not (g["min"]<=price<=g["max"])

def _norm(s:str)->str: return re.sub(r"\s+","",(s or "")).lower()
def _contains_any(text:str, kws:List[str])->bool:
    t=_norm(text); return any(_norm(k) in t for k in kws)

ACCESSORY_CORE={
    IPHONE:["케이스","범퍼","젤리","실리콘","필름","보호필름","강화유리","거치대","스탠드","팝소켓","링","스트랩",
            "충전기","케이블","라이트닝","type-c","어댑터","배터리팩","보조배터리","무선충전기","도킹","도킹스테이션",
            "magsafe case","magnetic case","case","bumper","jelly","silicone","film","protector","screen protector",
            "holder","dock","charger","cable","adapter"],
    IPAD:["케이스","커버","스마트커버","폴리오","키보드케이스","키보드","펜슬팁","펜촉","필름","강화유리","거치대",
          "스탠드","크래들","충전기","케이블","paperlike","smart cover","folio","pencil tip","holder","dock","stand","charger"],
    MACBOOK:["파우치","슬리브","케이스","하드케이스","키스킨","키보드 스킨","키캡","필름","강화유리","스탠드","거치대","독",
             "허브","usb 허브","type-c 허브","도킹스테이션","어댑터","충전기","연장케이블","쿨러","쿨링패드","sleeve","pouch",
             "shell case","keyboard cover","dock","hub","adapter","stand","cooler"],
    WATCH:["밴드","스트랩","가죽밴드","메탈밴드","나이키밴드","케이스","범퍼","보호필름","강화유리","충전기","충전독","충전스탠드",
           "band","strap","loop","link","case","bumper","film","charger","dock","stand"],
    AIRPODS:["케이스","실리콘케이스","하드케이스","가죽케이스","키링","이어팁","폼팁","스트랩","충전기","충전케이블",
             "충전케이스(빈 케이스)","보호필름","case","tip","ear tip","foam tip","strap","charger"],
}
DEVICE_STRONG_HINTS={
    IPHONE:["본체","풀박스","영수증","자급제","미개봉","리퍼","공기계","정품등록","아이클라우드","icloud","배터리성능",
            "배터리 사이클","사이클","개통","유심","용량","128gb","256gb","512gb","1tb"],
    IPAD:["본체","풀박스","영수증","자급제","미개봉","리퍼","wifi","cellular","lte","용량",
          "128gb","256gb","512gb","1tb","2tb","11형","12.9","10.9","10.2","9.7"],
    MACBOOK:["본체","풀박스","영수증","m1","m2","m3","intel","i5","i7","ram","ssd","배터리 사이클","사이클",
             "13인치","14인치","15인치","16인치"],
    WATCH:["본체","풀박스","울트라","se","gps","cellular","나이키","41mm","45mm","49mm","40mm","44mm",
           "stainless","aluminum","티타늄"],
    AIRPODS:["본체","충전케이스 포함","미개봉","정품 등록","정품 시리얼","시리얼","case 포함"],
}

ACCESSORY_ONLY_HINTS=["전용","호환","for","용","단품","케이스만","필름만","스트랩만","밴드만","케이블만",
                      "충전케이스 단품","충전기만","허브만","독만","stand only","case only","band only"]

INCLUSION_PHRASES=["케이스 포함","필름 부착","필름 붙임","사은품","덤으로","증정","케이스 드림","필름 드림"]

# ---------- 매입/구매/수요/수리/서비스 광고 필터 ----------
BUYING_HINTS = [
    "삽니다","구매합니다","구해요","찾습니다",
    "매입","고가매입","최고가매입","당일매입","전국매입","현금매입","매집",
    "매입합니다","매입해요","고가매수"
]
SERVICE_HINTS = [
    "수리","교체","수선","출장수리","사설수리","액정수리","배터리교체","보드수리",
    "위탁판매","대여","렌탈","보험","as","a/s","리퍼대행","출장"
]


def is_buying_or_service_title(title: str) -> bool:
    """True면 매입/구매/수요/수리/서비스성 광고로 간주(제외)"""
    if not title:
        return False
    t = re.sub(r"\s+","",title.lower())
    return any(k.replace(" ","") in t for k in BUYING_HINTS) or any(k.replace(" ","") in t for k in SERVICE_HINTS)


def is_accessory_title(title:str, category_id:int, price:Optional[int]=None, baseline_mean:Optional[float]=None)->bool:
    if not ENABLE_TITLE_FILTER or not title: return False
    t=_norm(title)
    if not _contains_any(t, ACCESSORY_CORE.get(category_id, [])): return False
    if _contains_any(t, INCLUSION_PHRASES): return False
    if _contains_any(t, ACCESSORY_ONLY_HINTS): return True
    if _contains_any(t, DEVICE_STRONG_HINTS.get(category_id, [])): return False
    if price is not None and baseline_mean and price < max(50_000, baseline_mean*0.25): return True
    return True

OUTLIER_RATIO=0.50; MIN_SAMPLES_FOR_RUNNING=20
def load_baselines():
    global baselines
    if os.path.exists(BASELINE_JSON):
        try:
            with open(BASELINE_JSON,"r",encoding="utf-8") as f: baselines=json.load(f) or {}
        except Exception: baselines={}
    else: baselines={}
def update_running_mean(category_id:int, price:int):
    st=running_stats.setdefault(category_id,{"n":0.0,"mean":0.0}); n=st["n"]; m=st["mean"]
    n2=n+1.0; st["n"]=n2; st["mean"]= m + (price - m)/n2 if n2>0 else price
def get_baseline_mean(category_id:int)->Optional[float]:
    m=baselines.get(str(category_id),{}).get("mean")
    if isinstance(m,(int,float)): return float(m)
    st=running_stats.get(category_id)
    if st and st.get("n",0)>=MIN_SAMPLES_FOR_RUNNING: return float(st["mean"])
    return None
def price_is_outlier(category_id:int, price:Optional[int])->bool:
    if not ENABLE_PRICE_FILTER or price is None: return False
    base=get_baseline_mean(category_id)
    if base is None or base<=0: return False
    lo,hi=base*(1.0-OUTLIER_RATIO), base*(1.0+OUTLIER_RATIO)
    return not (lo<=price<=hi)

def ensure_csv_header(path:str):
    need_header= (not os.path.exists(path)) or os.path.getsize(path)==0
    if need_header:
        with open(path,"w",newline="",encoding="utf-8") as f:
            csv.writer(f).writerow(CSV_COLS)
async def append_row(row:dict):
    ensure_csv_header(OUTPUT_CSV)
    async with csv_lock:
        with open(OUTPUT_CSV,"a",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=CSV_COLS,extrasaction="ignore")
            w.writerow(row); f.flush()
            try: os.fsync(f.fileno())
            except Exception: pass

def load_checkpoint()->dict:
    if not os.path.exists(CHECKPOINT): return {}
    try:
        with open(CHECKPOINT,"r",encoding="utf-8") as f: return json.load(f)
    except Exception: return {}
async def save_checkpoint(data:dict):
    async with checkpoint_lock:
        tmp=CHECKPOINT+".part"
        with open(tmp,"w",encoding="utf-8") as f:
            json.dump(data,f,ensure_ascii=False,indent=2); f.flush()
            try: os.fsync(f.fileno())
            except Exception: pass
        os.replace(tmp,CHECKPOINT)

def to_iso_utc_now()->str: return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def to_iso_utc(dt_text:str)->Optional[str]:
    if not dt_text: return None
    return dt_text if re.match(r"^\d{4}-\d{2}-\d{2}T",dt_text) else None
def parse_price_int(price_text:str)->Optional[int]:
    if not price_text: return None
    digits=re.sub(r"[^\d]","",price_text)
    if digits=="": return None
    try: return int(digits)
    except ValueError: return None

ARTICLE_NUM_RE = re.compile(r"/articles/(\d+)")
BUYSELL_SLUG_ID_RE = re.compile(r"/kr/buy-sell/[^/?#]*-([a-z0-9]{6,})", re.IGNORECASE)
def extract_external_id_url(url: str) -> Optional[str]:
    m = re.search(r"/articles/(\d+)", url)
    if m:
        return m.group(1)
    m2 = BUYSELL_SLUG_ID_RE.search(url)
    if m2:
        return m2.group(1)
    return None

async def try_selectors_get_text(page, selectors:List[str])->str:
    for sel in selectors:
        try:
            el=await page.query_selector(sel)
            if el:
                txt=(await el.inner_text()).strip()
                if txt: return txt
        except Exception: pass
    return ""

async def try_get_time_attr(page)->str:
    try:
        el=await page.query_selector("time[datetime]")
        if el:
            dt=await el.get_attribute("datetime")
            if dt: return dt.strip()
    except Exception: pass
    return await try_selectors_get_text(page, TIME_SELECTORS)

async def extract_dong_inparams_from_gu(page, gu_name:str)->List[Tuple[str,str]]:
    anchors=await page.query_selector_all("a[href*='/kr/buy-sell'], a[href*='?in=']")
    seen={}
    for a in anchors:
        href=await a.get_attribute("href")
        if not href: continue
        abs_href=urljoin(BASE,href) if href.startswith("/") or not href.startswith("http") else href
        parsed=urlparse(abs_href); qs=parse_qs(parsed.query)
        in_vals=qs.get("in") or qs.get("in[]") or []
        if not in_vals: continue
        in_param=in_vals[0]; 
        if not in_param: continue
        dong_name=unquote(in_param.split("-",1)[0]) if "-" in in_param else unquote(in_param)
        if dong_name and dong_name not in seen: seen[dong_name]=in_param
    return [(k,v) for k,v in seen.items()]

async def collect_anchor_hrefs_from_page(page)->List[str]:
    hrefs=[]
    try:
        els=await page.query_selector_all(LIST_LINK_SELECTOR)
        for e in els:
            h=await e.get_attribute("href")
            if h: hrefs.append(urljoin(BASE,h) if h.startswith("/") or not h.startswith("http") else h)
    except Exception: pass
    if not hrefs:
        for sel in PRIORITY_SELECTORS:
            try:
                els=await page.query_selector_all(sel)
                for e in els:
                    h=await e.get_attribute("href")
                    if h: hrefs.append(urljoin(BASE,h) if h.startswith("/") or not h.startswith("http") else h)
            except Exception: continue
    seen=set(); out=[]
    for h in hrefs:
        if h not in seen: seen.add(h); out.append(h)
    return out

async def extract_detail(context, url, city, gu, dong, category_id:int, semaphore):
    global stop_flag
    async with semaphore:
        if stop_flag: return
        page=await context.new_page()
        try:
            async def route_handler(route, request):
                if request.resource_type in ("image","stylesheet","font","media"):
                    await route.abort()
                else: await route.continue_()
            await page.route("**/*", route_handler)
            await page.goto(url, wait_until="networkidle", timeout=20000)
            await asyncio.sleep(0.2+random.random()*0.4)

            title=await try_selectors_get_text(page, TITLE_SELECTORS)
            price_txt=await try_selectors_get_text(page, PRICE_SELECTORS)
            posted_raw=await try_get_time_attr(page)

            # ----- 판매완료/예약중 판정 (h1 바로 이전 형제/그 내부) -----
            status="active"
            try:
                h1_el = await page.query_selector("h1")
                if h1_el:
                    flag = await h1_el.evaluate("""
                        (el) => {
                            // 이전 형제들 3개까지 검사
                            let prev = el.previousElementSibling;
                            const reSold = /판매완료/;
                            const reReserved = /예약중/;
                            let sold=false, reserved=false;

                            for (let i=0; i<3 && prev; i++){
                                const directTxt = (prev.textContent||'').trim();
                                if (reSold.test(directTxt)) sold=true;
                                if (reReserved.test(directTxt)) reserved=true;
                                const nodes = [...prev.querySelectorAll('span,div,button')];
                                for (const n of nodes){
                                    const t=(n.textContent||'').trim();
                                    if (reSold.test(t)) sold=true;
                                    if (reReserved.test(t)) reserved=true;
                                }
                                if (sold || reserved) break;
                                prev = prev.previousElementSibling;
                            }
                            // 부모 2단계 내 배지 확인 (h1 앞쪽에 위치하는지)
                            if (!sold && !reserved){
                                let p = el.parentElement;
                                for (let depth=0; depth<2 && p; depth++){
                                    const badgeNodes=[...p.querySelectorAll('span,div,button')];
                                    for (const b of badgeNodes){
                                        const t=(b.textContent||'').trim();
                                        const beforeH1 = !!(b.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
                                        if (!beforeH1) continue;
                                        if (reSold.test(t)) { sold=true; break; }
                                        if (reReserved.test(t)) { reserved=true; break; }
                                    }
                                    if (sold || reserved) break;
                                    p = p.parentElement;
                                }
                            }
                            return {sold, reserved};
                        }
                    """)
                    if flag and flag.get("sold"):
                        status="sold"; print(f"        [SOLD] {url}")
                    elif flag and flag.get("reserved"):
                        status="reserved"; print(f"        [RESERVED] {url}")
            except Exception as e:
                print(f"        [상태 배지 검사 오류] {url} - {e}")

            price_val=parse_price_int(price_txt)
            external_id=extract_external_id_url(url)
            last_crawled_at=to_iso_utc_now()
            posted_at=to_iso_utc(posted_raw)

            if is_buying_or_service_title(title): return
            if ENABLE_PRICE_GUARD and price_is_ridiculous(category_id, price_val): return
            baseline_mean=get_baseline_mean(category_id) if ENABLE_PRICE_FILTER else None
            if is_accessory_title(title, category_id, price_val, baseline_mean): return
            if price_is_outlier(category_id, price_val): return
            if price_val is not None: update_running_mean(category_id, price_val)

            row={"source":"daangn","external_id":external_id or "","category_id":category_id,"title":title or "",
                 "price": price_val if price_val is not None else "", "url":url, "status":status,
                 "sd":city,"sgg":gu,"emd":dong or "", "posted_at":posted_at or "",
                 "posted_updated_at":"", "last_crawled_at":last_crawled_at}
            await append_row(row)
        except PlaywrightTimeoutError:
            print("Timeout on detail:", url)
        except Exception as e:
            print("Detail exception:", e, url)
        finally:
            await page.close(); await asyncio.sleep(0.12+random.random()*0.4)

async def crawl_dong(context, city:str, gu:str, dong_name:str, in_param:str,
                     query:str, category_id:int, semaphore, checkpoint:dict):
    global stop_flag
    if stop_flag: return
    page=await context.new_page()
    try:
        if in_param:
            base_region=f"{BASE}/kr/buy-sell/?in={quote(in_param)}"
        else:
            city_p=quote(city); gu_p=quote(gu); dong_p=quote(dong_name) if dong_name else ""
            base_region=f"{BASE}/region/{city_p}/{gu_p}/{dong_p}" if dong_p else f"{BASE}/region/{city_p}/{gu_p}"
        start_url=base_region + (("&search="+quote(query)) if query else "")
        print(f"    [{query}] {gu}/{dong_name} -> {start_url}")
        try:
            await page.goto(start_url, wait_until="networkidle", timeout=20000)
        except PlaywrightTimeoutError:
            print("    목록타임아웃:", start_url); await page.close(); return

        async def route_handler(route, request):
            if request.resource_type in ("image","stylesheet","font","media"): await route.abort()
            else: await route.continue_()
        await page.route("**/*", route_handler)

        collected=set(); detail_tasks=[]; no_new_rounds=0
        MORE_BUTTON_SELECTORS=["button:has-text('더보기')","button:has-text('더 불러오기')",
                               "a.load-more",".load-more","button.load-more","button#more","a[role='button']"]

        for rnd in range(MAX_SCROLL_ROUNDS):
            if stop_flag: break
            hrefs_found=await collect_anchor_hrefs_from_page(page)
            new_found=0
            for h in hrefs_found:
                if stop_flag: break
                if h not in collected:
                    collected.add(h); new_found+=1
                    detail_tasks.append(asyncio.create_task(
                        extract_detail(context,h,city,gu,dong_name,category_id,semaphore)))
            print(f"      round {rnd+1}: total links {len(collected)} (+{new_found})")

            clicked=False
            for btn_sel in MORE_BUTTON_SELECTORS:
                try:
                    btn=await page.query_selector(btn_sel)
                    if btn:
                        try:
                            await btn.click(); clicked=True
                            await asyncio.sleep(0.8+random.random()*0.8); break
                        except Exception: continue
                except Exception: continue

            if clicked: no_new_rounds=0
            else:
                no_new_rounds = 0 if new_found>0 else (no_new_rounds+1)
                if no_new_rounds>=2: break
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                await asyncio.sleep(random.uniform(*SCROLL_PAUSE))

        if detail_tasks: await asyncio.gather(*detail_tasks)

        if not stop_flag and len(collected)<30:
            page_obj=await context.new_page()
            try:
                print(f"      페일백: page=N 방식 시도 {gu}/{dong_name}")
                collected2=set(collected); detail_tasks2=[]
                for pnum in range(1,MAX_PAGES+1):
                    if stop_flag: break
                    qparts=[]; 
                    if query: qparts.append(f"search={quote(query)}")
                    qparts.append(f"page={pnum}")
                    page_url=base_region + ("&" if "?" in base_region else "?") + "&".join(qparts)
                    try:
                        await page_obj.goto(page_url, wait_until="networkidle", timeout=15000)
                    except PlaywrightTimeoutError:
                        print("        page timeout:", page_url); break
                    hrefs=await collect_anchor_hrefs_from_page(page_obj)
                    new_found=0
                    for h in hrefs:
                        if stop_flag: break
                        if h not in collected2:
                            collected2.add(h); new_found+=1
                            detail_tasks2.append(asyncio.create_task(
                                extract_detail(context,h,city,gu,dong_name,category_id,semaphore)))
                    print(f"        page {pnum}: total links {len(collected2)} (+{new_found})")
                    if new_found==0: break
                if detail_tasks2: await asyncio.gather(*detail_tasks2)
            finally:
                await page_obj.close()

        cp_q=checkpoint.setdefault(query,{}).setdefault(gu,{})
        cp_q["last_done_dong"]=dong_name; cp_q["last_done_page"]=None
        await save_checkpoint(checkpoint)
        await page.close()
    except Exception as e:
        print("crawl_dong exception:", e, gu, dong_name)
        try: await page.close()
        except Exception: pass

async def crawl_all_seoul_for_query(context, query:str, category_id:int):
    city="서울특별시"; checkpoint=load_checkpoint(); done_map=checkpoint.get(query,{})
    for gu in SEOUL_GU:
        if stop_flag: return
        print(f"[{query}] 구 접근:", gu)
        page=await context.new_page()
        try:
            gu_url=f"{BASE}/region/{quote(city)}/{quote(gu)}"
            try: await page.goto(gu_url, wait_until="networkidle", timeout=20000)
            except PlaywrightTimeoutError:
                print("  구 페이지 타임아웃:", gu_url); await page.close(); continue
            dongs_info=await extract_dong_inparams_from_gu(page, gu)
            if not dongs_info:
                print("  동 목록 자동추출 실패, 구 자체 페이지로 폴백:", gu); dongs_info=[(gu,None)]
            print(f"  {gu}에서 추출된 동 개수:", len(dongs_info))
            await page.close()

            gu_cp=done_map.get(gu,{}); last_done_dong=gu_cp.get("last_done_dong"); skip=bool(last_done_dong)
            semaphore=asyncio.Semaphore(CONCURRENCY)

            for dong_name,in_param in dongs_info:
                if stop_flag: return
                if skip:
                    if dong_name==last_done_dong: skip=False
                    continue
                await crawl_dong(context, city, gu, dong_name, in_param, query, category_id, semaphore, checkpoint)
                await asyncio.sleep(0.25+random.random()*0.45)
        except Exception as e:
            print("구 처리 예외:", e, gu)
            try: await page.close()
            except Exception: pass

def install_signal_handlers():
    def handler(signum, frame):
        global stop_flag; stop_flag=True
        print(f"\n[신호] {signum} 수신: 안전 종료 진행 중... (CSV/체크포인트 보존)")
    try:
        signal.signal(signal.SIGINT, handler); signal.signal(signal.SIGTERM, handler)
    except Exception: pass

async def main():
    load_baselines(); install_signal_handlers()
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=HEADLESS)
        context=await browser.new_context(user_agent=USER_AGENT)
        for query,category_id in CATEGORY_MAP.items():
            if stop_flag: break
            print(f"\n==== 키워드 시작: {query} (category_id={category_id}) ====")
            await crawl_all_seoul_for_query(context, query, category_id)
        await context.close(); await browser.close()

if __name__=="__main__":
    asyncio.run(main())
