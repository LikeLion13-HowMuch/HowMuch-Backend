from typing import Protocol, Iterable
from app.schemas.items import RawItem
from app.schemas.common import MarketSource

class Scraper(Protocol):
    source: MarketSource
    async def search(self, query: str, limit: int = 100) -> Iterable[RawItem]: ...
