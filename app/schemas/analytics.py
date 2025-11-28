from typing import Optional, List
from pydantic import BaseModel, Field

class Spec(BaseModel):
    model: Optional[str] = None
    storage: Optional[str] = None
    color: Optional[str] = None
    chip: Optional[str] = None
    ram: Optional[str] = None
    screen_size: Optional[str] = None
    size: Optional[str] = None
    material: Optional[str] = None
    connectivity: Optional[str] = None
    cellular: Optional[str] = None
    pencil_support: Optional[bool] = None

class RegionFilter(BaseModel):
    sd: Optional[str] = None
    sgg: Optional[str] = None
    emd: Optional[str] = None

class PriceTrendPoint(BaseModel):
    period: str
    price: int

class DistrictDetail(BaseModel):
    sgg: Optional[str] = None
    emd: str
    average_price: int
    listing_count: int

class SummaryInfo(BaseModel):
    model_name: str
    average_price: int
    highest_listing_price: int
    lowest_listing_price: int
    listing_count: int
    data_date: str

class RegionalAnalysis(BaseModel):
    detail_by_district: List[DistrictDetail]

class PriceTrend(BaseModel):
    trend_period: int
    change_rate: float
    chart_data: List[PriceTrendPoint]

class LowestListing(BaseModel):
    listing_price: int
    district_detail: str
    source: str
    source_url: str

class AnalyticsRequest(BaseModel):
    product: str = Field(..., description="iPhone, MacBook, iPad, AppleWatch, AirPods")
    spec: Spec
    region: RegionFilter

class AnalyticsResponse(BaseModel):
    status: str
    data: dict
