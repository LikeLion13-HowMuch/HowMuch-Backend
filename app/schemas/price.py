"""
Pydantic Models (Request/Response)
"""
from typing import List, Optional
from pydantic import BaseModel, Field


# ============================================
# Request Models
# ============================================

class RegionRequest(BaseModel):
    """지역 정보 Request"""
    sd: Optional[str] = Field(None, description="시도 (ex. 서울특별시, 경기도)")
    sgg: Optional[str] = Field(None, description="시군구 (ex. 강남구, 성남시)")
    emd: Optional[str] = Field(None, description="읍면동 (ex. 역삼동, 분당동)")


class SpecRequest(BaseModel):
    """제품 스펙 Request"""
    # 공통 필드
    model: Optional[str] = Field(None, description="모델명 (ex. iPhone 14 Pro, MacBook Air M2)")
    storage: Optional[str] = Field(None, description="용량 (ex. 256GB)")
    color: Optional[str] = Field(None, description="색상 (ex. 티타늄 블랙)")

    # MacBook 전용
    chip: Optional[str] = Field(None, description="칩셋 (ex. M2, M3, M4)")
    ram: Optional[str] = Field(None, description="램 (ex. 16GB)")
    screen_size: Optional[str] = Field(None, description="화면 크기 (ex. 13-inch, 12.9-inch)")

    # AppleWatch 전용
    size: Optional[str] = Field(None, description="Watch 케이스 크기 (ex. 49mm)")
    material: Optional[str] = Field(None, description="Watch 본체 소재 (ex. 스테인리스스틸, 티타늄)")
    connectivity: Optional[str] = Field(None, description="Watch 연결 방식 (ex. GPS, GPS + 셀룰러)")

    # iPad 전용
    cellular: Optional[str] = Field(None, description="iPad 셀룰러 옵션 (ex. Wi-Fi, Wi-Fi + Cellular)")
    pencil_support: Optional[bool] = Field(None, description="iPad 펜슬 지원 여부")


class ProductPriceRequest(BaseModel):
    """제품 시세 조회 Request"""
    product: str = Field(..., description="제품 카테고리 (iPhone, MacBook, iPad, AppleWatch, AirPods)")
    spec: SpecRequest
    region: RegionRequest


# ============================================
# Response Models
# ============================================

class SummaryInfo(BaseModel):
    """요약 정보"""
    model_name: str = Field(..., description="모델명")
    average_price: int = Field(..., description="해당 emd의 평균 시세")
    highest_listing_price: int = Field(..., description="최고가")
    lowest_listing_price: int = Field(..., description="최저가")
    listing_count: int = Field(..., description="해당 emd의 상품 개수")
    data_date: str = Field(..., description="데이터 기준일 (ex. 2025-11-19 17:00)")


class DistrictDetail(BaseModel):
    """읍면동별 상세 정보"""
    emd: str = Field(..., description="읍면동")
    average_price: int = Field(..., description="평균 가격")
    listing_count: int = Field(..., description="상품 개수")


class RegionalAnalysis(BaseModel):
    """지역별 분석"""
    detail_by_district: List[DistrictDetail]


class ChartDataPoint(BaseModel):
    """차트 데이터 포인트"""
    period: str = Field(..., description="기간 (ex. 1월 1주)")
    price: int = Field(..., description="평균 가격")


class PriceTrend(BaseModel):
    """가격 추이"""
    trend_period: int = Field(..., description="분석 기간 (주)")
    change_rate: float = Field(..., description="변화율 (%)")
    chart_data: List[ChartDataPoint]


class Listing(BaseModel):
    """매물 정보"""
    listing_price: int = Field(..., description="매물 가격")
    district_detail: str = Field(..., description="지역 상세 (ex. 관악구 신림동)")
    source: str = Field(..., description="출처 (ex. 중고나라)")
    source_url: str = Field(..., description="매물 링크")


class ProductPriceData(BaseModel):
    """제품 가격 데이터"""
    summary_info: SummaryInfo
    regional_analysis: RegionalAnalysis
    price_trend: PriceTrend
    lowest_price_listings: List[Listing]


class ProductPriceResponse(BaseModel):
    """제품 시세 조회 Response"""
    status: str = Field(..., description="응답 상태 (success/error)")
    data: Optional[ProductPriceData] = None
    message: Optional[str] = Field(None, description="에러 메시지")
