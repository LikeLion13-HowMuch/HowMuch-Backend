# app/crawlers/bunjang.py
from typing import AsyncIterator, Sequence
import asyncio
import httpx
from urllib.parse import quote

from app.schemas.items import RawItem
from app.schemas.common import MarketSource

# ====== Constants & Settings ======
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
DEFAULT_KEYWORDS = ["아이폰", "아이패드", "맥북", "애플워치", "에어팟"]
LIMIT_PER_PAGE = 96  # Bunjang API returns about 96 items per page

# ====== Scraper Implementation ======
class BunjangScraper:
    """
    Scraper for Bunjang (번개장터) using its internal API.
    Conforms to the Scraper protocol defined in base.py.
    """
    source = MarketSource.bunjang

    async def search(self, query: str, limit: int = 100) -> AsyncIterator[RawItem]:
        """
        Asynchronously searches for products on Bunjang and yields RawItem objects.
        """
        page = 0
        items_collected = 0

        async with httpx.AsyncClient(headers=HEADERS, timeout=20.0) as client:
            while items_collected < limit:
                params = {
                    "q": query,
                    "order": "score",
                    "page": page,
                    "n": LIMIT_PER_PAGE,
                    "req_ref": "search",
                    "stat_device": "w",
                    "version": "5",
                }

                try:
                    resp = await client.get(API_BASE_URL, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                except (httpx.RequestError, httpx.HTTPStatusError) as e:
                    print(f"Bunjang API request failed: {e}")
                    break

                if data.get("result") != "success" or not data.get("list"):
                    # No more items or an API error message
                    break

                for item in data["list"]:
                    if items_collected >= limit:
                        break

                    # Filter out ads and non-product listings
                    if item.get("ad") or item.get("type") != "PRODUCT":
                        continue

                    pid = item.get("pid")
                    if not pid:
                        continue

                    price_str = item.get("price", "0")
                    
                    # Parse location
                    location_str = item.get("location")
                    sd, sgg, emd = None, None, None
                    if location_str:
                        parts = location_str.strip().split()
                        if len(parts) >= 1:
                            sd = parts[0]
                        if len(parts) >= 2:
                            sgg = parts[1]
                        if len(parts) >= 3:
                            emd = " ".join(parts[2:])

                    yield RawItem(
                        source=self.source,
                        external_id=pid,
                        title=item.get("name", ""),
                        price_text=price_str,
                        price=int(price_str) if price_str.isdigit() else None,
                        url=f"https://m.bunjang.co.kr/products/{pid}",
                        sd=sd,
                        sgg=sgg,
                        emd=emd,
                    )
                    items_collected += 1

                page += 1
                await asyncio.sleep(0.5) # Be nice to the API

    async def crawl_keywords(
        self,
        keywords: Sequence[str] = DEFAULT_KEYWORDS,
        limit_per_keyword: int = 100,
    ) -> AsyncIterator[RawItem]:
        """
        Crawls multiple keywords in sequence.
        """
        for kw in keywords:
            async for item in self.search(kw, limit=limit_per_keyword):
                yield item