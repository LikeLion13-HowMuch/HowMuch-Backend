from typing import Iterable, AsyncIterator, List, Tuple, Optional, Sequence
import asyncio, random, re
from urllib.parse import urljoin, urlparse, parse_qs, unquote, quote

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from app.schemas.items import RawItem
from app.schemas.common import MarketSource

# ====== 상수/설정 ======
BASE = "https://www.daangn.com"
HEADLESS = True
CONCURRENCY = 4
MAX_SCROLL_ROUNDS = 2
SCROLL_PAUSE = (0.6, 1.0)
MAX_PAGES = 30
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36")

# 서울 구 목록(필요시 외부 설정으로 뺄 수 있음)
SEOUL_GU = [
    "종로구","중구","용산구","성동구","광진구","동대문구","중랑구","성북구","강북구","도봉구",
    "노원구","은평구","서대문구","마포구","양천구","강서구","구로구","금천구","영등포구","동작구",
    "관악구","서초구","강남구","송파구","강동구"
]

# 키워드 기본값(아이폰/아이패드/맥북/애플워치/에어팟)
DEFAULT_KEYWORDS = ["아이폰", "아이패드", "맥북", "애플워치", "에어팟"]

# 선택자 (원본 로직 기반)  :contentReference[oaicite:1]{index=1}
TITLE_SELECTORS = ["h1"]
PRICE_SELECTORS = ["h3"]
TIME_SELECTORS  = ["time[datetime]", "time"]
LIST_LINK_SELECTOR = 'div[data-gtm="search_article"] a'
PRIORITY_SELECTORS = ['a[data-gtm="search_article"]', "a[href*='/articles/']"]


# ====== 유틸 ======
def _extract_external_id(url: str) -> str:
    # /articles/123456789 -> 123456789, 없으면 URL 전체
    m = re.search(r"/articles/(\d+)", url)
    return m.group(1) if m else url

async def _try_selectors_get_text(page, selectors: List[str]) -> str:
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                txt = (await el.inner_text()).strip()
                if txt:
                    return txt
        except Exception:
            pass
    return ""

async def _collect_anchor_hrefs_from_page(page) -> List[str]:
    hrefs = []
    try:
        els = await page.query_selector_all(LIST_LINK_SELECTOR)
        for e in els:
            h = await e.get_attribute("href")
            if h:
                hrefs.append(urljoin(BASE, h) if h.startswith("/") or not h.startswith("http") else h)
    except Exception:
        pass
    if not hrefs:
        for sel in PRIORITY_SELECTORS:
            try:
                els = await page.query_selector_all(sel)
                for e in els:
                    h = await e.get_attribute("href")
                    if h:
                        hrefs.append(urljoin(BASE, h) if h.startswith("/") or not h.startswith("http") else h)
            except Exception:
                continue
    seen = set(); out = []
    for h in hrefs:
        if h not in seen:
            seen.add(h); out.append(h)
    return out

async def _extract_dong_inparams_from_gu(page, gu_name: str) -> List[Tuple[str, str]]:
    anchors = await page.query_selector_all("a[href*='/kr/buy-sell'], a[href*='?in=']")
    seen = {}
    for a in anchors:
        href = await a.get_attribute("href")
        if not href:
            continue
        abs_href = urljoin(BASE, href) if href.startswith("/") or not href.startswith("http") else href
        parsed = urlparse(abs_href)
        qs = parse_qs(parsed.query)
        in_vals = qs.get("in") or qs.get("in[]") or []
        if not in_vals:
            continue
        in_param = in_vals[0]
        if not in_param:
            continue
        dong_name = unquote(in_param.split("-", 1)[0]) if "-" in in_param else unquote(in_param)
        if dong_name and dong_name not in seen:
            seen[dong_name] = in_param
    return [(k, v) for k, v in seen.items()]

async def _extract_detail(context, url, city, gu, dong, semaphore, results_list):
    async with semaphore:
        page = await context.new_page()
        try:
            async def route_handler(route, request):
                if request.resource_type in ("image", "stylesheet", "font", "media"):
                    await route.abort()
                else:
                    await route.continue_()
            await page.route("**/*", route_handler)

            await page.goto(url, wait_until="networkidle", timeout=20000)
            await asyncio.sleep(0.2 + random.random()*0.4)

            title = await _try_selectors_get_text(page, TITLE_SELECTORS)
            price = await _try_selectors_get_text(page, PRICE_SELECTORS)
            posted_time = await _try_selectors_get_text(page, TIME_SELECTORS)

            results_list.append({
                "city": city,
                "gu": gu,
                "dong": dong,
                "title": title or "",
                "price": price or "",
                "posted_time": posted_time or "",
                "url": url
            })
        except TimeoutError:
            pass
        except PlaywrightTimeoutError:
            pass
        finally:
            await page.close()
            await asyncio.sleep(0.12 + random.random()*0.4)

async def _crawl_dong(context, query: str, city: str, gu: str, dong_name: str, in_param: Optional[str],
                      semaphore, results_list):
    page = await context.new_page()
    try:
        if in_param:
            base_region = f"{BASE}/kr/buy-sell/?in={quote(in_param)}"
        else:
            city_p = quote(city); gu_p = quote(gu); dong_p = quote(dong_name) if dong_name else ""
            base_region = f"{BASE}/region/{city_p}/{gu_p}/{dong_p}" if dong_p else f"{BASE}/region/{city_p}/{gu_p}"

        start_url = base_region + (("&" if "?" in base_region else "?") + f"search={quote(query)}")
        try:
            await page.goto(start_url, wait_until="networkidle", timeout=20000)
        except PlaywrightTimeoutError:
            await page.close(); return

        async def route_handler(route, request):
            if request.resource_type in ("image", "stylesheet", "font", "media"):
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", route_handler)

        collected = set()
        detail_tasks = []
        no_new_rounds = 0

        MORE_BUTTON_SELECTORS = [
            "button:has-text('더보기')","button:has-text('더 불러오기')",
            "a.load-more",".load-more","button.load-more","button#more","a[role='button']"
        ]

        for _ in range(MAX_SCROLL_ROUNDS):
            hrefs_found = await _collect_anchor_hrefs_from_page(page)
            new_found = 0
            for h in hrefs_found:
                if h not in collected:
                    collected.add(h); new_found += 1
                    detail_tasks.append(asyncio.create_task(
                        _extract_detail(context, h, city, gu, dong_name, semaphore, results_list)
                    ))

            clicked = False
            for btn_sel in MORE_BUTTON_SELECTORS:
                try:
                    btn = await page.query_selector(btn_sel)
                    if btn:
                        try:
                            await btn.click()
                            clicked = True
                            await asyncio.sleep(0.8 + random.random()*0.8)
                            break
                        except Exception:
                            continue
                except Exception:
                    continue

            if clicked:
                no_new_rounds = 0
                continue

            if new_found == 0: no_new_rounds += 1
            else:              no_new_rounds = 0
            if no_new_rounds >= 2:
                break

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            await asyncio.sleep(random.uniform(*SCROLL_PAUSE))

        if detail_tasks:
            await asyncio.gather(*detail_tasks)

        # Fallback: page=N
        if len(collected) < 30:
            page_obj = await context.new_page()
            try:
                collected2 = set(collected)
                detail_tasks2 = []
                for pnum in range(1, MAX_PAGES+1):
                    qparts = [f"search={quote(query)}", f"page={pnum}"]
                    page_url = base_region + ("&" if "?" in base_region else "?") + "&".join(qparts)
                    try:
                        await page_obj.goto(page_url, wait_until="networkidle", timeout=15000)
                    except PlaywrightTimeoutError:
                        break

                    hrefs = await _collect_anchor_hrefs_from_page(page_obj)
                    new_found = 0
                    for h in hrefs:
                        if h not in collected2:
                            collected2.add(h); new_found += 1
                            detail_tasks2.append(asyncio.create_task(
                                _extract_detail(context, h, city, gu, dong_name, semaphore, results_list)
                            ))
                    if new_found == 0:
                        break
                if detail_tasks2:
                    await asyncio.gather(*detail_tasks2)
                for h in collected2:
                    collected.add(h)
            finally:
                await page_obj.close()

        await page.close()
    except Exception:
        try:
            await page.close()
        except:
            pass


# ====== Scraper 구현 ======
class DaangnScraper:
    """FastAPI용 크롤러 어댑터: RawItem 스트림을 반환.
       - search(query): 단일 키워드
       - crawl_keywords(keywords): 여러 키워드(아이폰/아이패드/맥북/애플워치/에어팟 등)
    """
    source = MarketSource.daangn

    async def search(self, query: str, limit: int = 200) -> AsyncIterator[RawItem]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=HEADLESS)
            context = await browser.new_context(user_agent=USER_AGENT)

            city = "서울특별시"
            semaphore = asyncio.Semaphore(CONCURRENCY)

            try:
                for gu in SEOUL_GU:
                    page = await context.new_page()
                    try:
                        gu_url = f"{BASE}/region/{quote(city)}/{quote(gu)}"
                        try:
                            await page.goto(gu_url, wait_until="networkidle", timeout=20000)
                        except PlaywrightTimeoutError:
                            await page.close(); continue

                        dongs_info = await _extract_dong_inparams_from_gu(page, gu)
                        if not dongs_info:
                            dongs_info = [(gu, None)]
                        await page.close()

                        results: List[dict] = []
                        for dong_name, in_param in dongs_info:
                            await _crawl_dong(context, query, city, gu, dong_name, in_param, semaphore, results)
                            if len(results) >= limit:
                                break

                        # dedupe by url & limit
                        seen_urls = set()
                        for r in results:
                            if r["url"] in seen_urls:
                                continue
                            seen_urls.add(r["url"])
                            yield RawItem(
                                source=self.source,
                                external_id=_extract_external_id(r["url"]),
                                title=r.get("title") or "",
                                price_text=r.get("price"),
                                price=None,
                                url=r["url"],
                                city=r.get("city"),
                                gu=r.get("gu"),
                                dong=r.get("dong"),
                            )
                            if len(seen_urls) >= limit:
                                break

                    except Exception:
                        try:
                            await page.close()
                        except:
                            pass

            finally:
                await context.close()
                await browser.close()

    async def crawl_keywords(self, keywords: Sequence[str] = DEFAULT_KEYWORDS, limit_per_keyword: int = 200) -> AsyncIterator[RawItem]:
        for kw in keywords:
            async for item in self.search(kw, limit=limit_per_keyword):
                yield item
