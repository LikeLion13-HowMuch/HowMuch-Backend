from pydantic import BaseModel, AnyHttpUrl
from typing import Optional
from app.schemas.common import MarketSource
from enum import Enum
from datetime import datetime

class ItemStatus(str, Enum):
    active = "active"
    reserved = "reserved"
    sold = "sold"
    hidden = "hidden"

class RawItem(BaseModel):
    source: MarketSource
    external_id: str
    category_id: int

    title: str
    price: int  
    url: AnyHttpUrl
    status: ItemStatus = ItemStatus.active

    sd: Optional[str] = None
    sgg: Optional[str] = None
    emd: Optional[str] = None

    posted_at: Optional[datetime] = None  
    posted_updated_at: Optional[datetime] = None
    last_crawled_at: Optional[datetime] = None
