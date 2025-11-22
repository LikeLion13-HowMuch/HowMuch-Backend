from pydantic import BaseModel, AnyHttpUrl
from typing import Optional
from app.schemas.common import MarketSource

class RawItem(BaseModel):
    source: MarketSource
    external_id: str
    title: Optional[str]
    price_text: Optional[str]
    price: Optional[int]         # 파싱된 정수
    url: AnyHttpUrl
    sd: Optional[str] = None
    sgg: Optional[str] = None
    emd: Optional[str] = None
    category_id: Optional[int] = None  # 미지정 시 API에서 기본 카테고리 주입
