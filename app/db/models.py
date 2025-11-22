"""
SQLAlchemy ORM Models
"""
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Numeric,
    TIMESTAMP, ForeignKey, Enum as SQLEnum, BIGINT
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy import UniqueConstraint, CheckConstraint
from app.db.session import Base
import enum


# ============================================
# Enum Types
# ============================================

class AttributeDataType(str, enum.Enum):
    """속성 데이터 타입"""
    text = "text"
    int = "int"
    decimal = "decimal"
    bool = "bool"
    enum = "enum"


class ItemStatus(str, enum.Enum):
    """아이템 상태"""
    active = "active"
    reserved = "reserved"
    sold = "sold"
    hidden = "hidden"


# ============================================
# 행정구역 테이블
# ============================================

class Sd(Base):
    """시도 (서울특별시, 경기도 등)"""
    __tablename__ = "sd"

    sd_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False, unique=True)

    # Relationships
    sggs = relationship("Sgg", back_populates="sd")


class Sgg(Base):
    """시군구 (송파구, 안양시 등)"""
    __tablename__ = "sgg"

    sgg_id = Column(Integer, primary_key=True, autoincrement=True)
    sd_id = Column(Integer, ForeignKey("sd.sd_id", ondelete="RESTRICT"), nullable=False)
    name = Column(String(50), nullable=False)

    # Relationships
    sd = relationship("Sd", back_populates="sggs")
    emds = relationship("Emd", back_populates="sgg")

    __table_args__ = (UniqueConstraint("sd_id", "name", name="ux_sgg_sd_name"),)


class Emd(Base):
    """읍면동 (잠실동, 평촌동 등)"""
    __tablename__ = "emd"

    region_id = Column(Integer, primary_key=True, autoincrement=True)
    sgg_id = Column(Integer, ForeignKey("sgg.sgg_id", ondelete="RESTRICT"), nullable=False)
    name = Column(String(50), nullable=False)

    # Relationships
    sgg = relationship("Sgg", back_populates="emds")
    items = relationship("Item", back_populates="region")
    price_stats = relationship("PriceStats", back_populates="region")

    __table_args__ = (UniqueConstraint("sgg_id", "name", name="ux_emd_sgg_name"),)


# ============================================
# 카테고리 및 속성
# ============================================

class Category(Base):
    """카테고리 (iPhone, MacBook 등)"""
    __tablename__ = "category"

    category_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)

    # Relationships
    items = relationship("Item", back_populates="category")
    skus = relationship("Sku", back_populates="category")
    category_attributes = relationship("CategoryAttribute", back_populates="category")


class Attribute(Base):
    """속성 정의 (모델, 용량, 색상 등)"""
    __tablename__ = "attributes"

    attribute_id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(100), nullable=False, unique=True)
    label = Column(String(100), nullable=False)
    datatype = Column(SQLEnum(AttributeDataType, name="attribute_datatype"), nullable=False)
    unit = Column(String(50))
    description = Column(Text)

    # Relationships
    attribute_options = relationship("AttributeOption", back_populates="attribute")
    category_attributes = relationship("CategoryAttribute", back_populates="attribute")
    item_attribute_values = relationship("ItemAttributeValue", back_populates="attribute")
    sku_attributes = relationship("SkuAttribute", back_populates="attribute")


class AttributeOption(Base):
    """속성 옵션 (모델명, 용량 값 등)"""
    __tablename__ = "attribute_options"

    option_id = Column(Integer, primary_key=True, autoincrement=True)
    attribute_id = Column(Integer, ForeignKey("attributes.attribute_id", ondelete="CASCADE"), nullable=False)
    value = Column(String(100), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)

    # Relationships
    attribute = relationship("Attribute", back_populates="attribute_options")

    __table_args__ = (UniqueConstraint("attribute_id", "value", name="ux_attr_opt_attr_value"),)


class CategoryAttribute(Base):
    """카테고리별 사용 속성"""
    __tablename__ = "category_attributes"

    category_id = Column(Integer, ForeignKey("category.category_id", ondelete="CASCADE"), primary_key=True)
    attribute_id = Column(Integer, ForeignKey("attributes.attribute_id", ondelete="CASCADE"), primary_key=True)
    is_required = Column(Boolean, nullable=False, default=False)
    display_group = Column(String(100))
    sort_order = Column(Integer, nullable=False, default=0)

    # Relationships
    category = relationship("Category", back_populates="category_attributes")
    attribute = relationship("Attribute", back_populates="category_attributes")


# ============================================
# 아이템
# ============================================

class Item(Base):
    """아이템 (중고거래 매물)"""
    __tablename__ = "items"

    item_id = Column(BIGINT, primary_key=True, autoincrement=True)
    sku_id    = Column(BIGINT, ForeignKey("sku.sku_id", ondelete="RESTRICT"), nullable=False)
    region_id = Column(Integer, ForeignKey("emd.region_id", ondelete="RESTRICT"), nullable=False)
    category_id = Column(Integer, ForeignKey("category.category_id", ondelete="RESTRICT"), nullable=False)

    title = Column(String(500), nullable=False)
    price = Column(Integer, nullable=False)
    status = Column(SQLEnum(ItemStatus, name="item_status"), nullable=False, default=ItemStatus.active)
    url = Column(Text, nullable=False)

    source = Column(String(20), nullable=False, default="daangn")
    external_id = Column(String(100), nullable=False)
    __table_args__ = (UniqueConstraint("source", "external_id", name="ux_items_source_external"),)


    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    # Relationships
    sku     = relationship("Sku", back_populates="items")
    region = relationship("Emd", back_populates="items")
    category = relationship("Category", back_populates="items")
    item_attribute_values = relationship("ItemAttributeValue", back_populates="item")


class ItemAttributeValue(Base):
    """아이템 속성 값 (EAV)"""
    __tablename__ = "item_attribute_values"

    item_id = Column(BIGINT, ForeignKey("items.item_id", ondelete="CASCADE"), primary_key=True)
    attribute_id = Column(Integer, ForeignKey("attributes.attribute_id", ondelete="CASCADE"), primary_key=True)
    option_id = Column(Integer, ForeignKey("attribute_options.option_id", ondelete="SET NULL"))
    value_text = Column(Text)
    value_int = Column(Integer)
    value_decimal = Column(Numeric(18, 4))
    value_bool = Column(Boolean)

    # Relationships
    item = relationship("Item", back_populates="item_attribute_values")
    attribute = relationship("Attribute", back_populates="item_attribute_values")

    __table_args__ = (
        CheckConstraint(
            "(value_text IS NOT NULL)::int + (value_int IS NOT NULL)::int + "
            "(value_decimal IS NOT NULL)::int + (value_bool IS NOT NULL)::int <= 1",
            name="chk_itemattr_single_value"
        ),
    )


# ============================================
# SKU
# ============================================

class Sku(Base):
    """SKU (Stock Keeping Unit)"""
    __tablename__ = "sku"

    sku_id = Column(BIGINT, primary_key=True, autoincrement=True)
    category_id = Column(Integer, ForeignKey("category.category_id", ondelete="RESTRICT"), nullable=False)
    fingerprint = Column(String(255), nullable=False, unique=True)

    # Relationships
    category = relationship("Category", back_populates="skus")
    sku_attributes = relationship("SkuAttribute", back_populates="sku")
    price_stats = relationship("PriceStats", back_populates="sku")


class SkuAttribute(Base):
    """SKU 속성"""
    __tablename__ = "sku_attribute"

    sku_id = Column(BIGINT, ForeignKey("sku.sku_id", ondelete="CASCADE"), primary_key=True)
    attribute_id = Column(Integer, ForeignKey("attributes.attribute_id", ondelete="CASCADE"), primary_key=True)
    option_id = Column(Integer, ForeignKey("attribute_options.option_id", ondelete="SET NULL"))
    value_text = Column(Text)
    value_int = Column(Integer)
    value_decimal = Column(Numeric(18, 4))
    value_bool = Column(Boolean)

    # Relationships
    sku = relationship("Sku", back_populates="sku_attributes")
    attribute = relationship("Attribute", back_populates="sku_attributes")

    __table_args__ = (
        CheckConstraint(
            "(value_text IS NOT NULL)::int + (value_int IS NOT NULL)::int + "
            "(value_decimal IS NOT NULL)::int + (value_bool IS NOT NULL)::int <= 1",
            name="chk_skuattr_single_value"
        ),
    )


# ============================================
# 가격 통계
# ============================================

class PriceStats(Base):
    """가격 통계"""
    __tablename__ = "price_stats"

    sku_id = Column(BIGINT, ForeignKey("sku.sku_id", ondelete="CASCADE"), primary_key=True)
    region_id = Column(Integer, ForeignKey("emd.region_id", ondelete="RESTRICT"), primary_key=True)
    bucket_ts = Column(TIMESTAMP(timezone=True), nullable=False, primary_key=True)
    items_num = Column(Integer, nullable=False, default=0)
    sum_price = Column(BIGINT, nullable=False, default=0)
    avg_price = Column(Numeric(18, 2))
    min_price = Column(Integer)
    max_price = Column(Integer)

    # Relationships
    sku = relationship("Sku", back_populates="price_stats")
    region = relationship("Emd", back_populates="price_stats")
