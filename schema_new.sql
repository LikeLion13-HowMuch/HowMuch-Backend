-- =========================================
-- 중고거래 플랫폼 데이터베이스 스키마 (신규 버전)
-- SKU 기반 가격 통계 기능 포함
-- =========================================

-- =========================================
-- 1) ENUM 타입 정의
-- =========================================

-- 속성 값의 데이터 타입
CREATE TYPE attribute_datatype AS ENUM (
  'text',      -- 문자열 (색상, 모델명 등)
  'int',       -- 정수 (용량, 숫자형 옵션 등)
  'decimal',   -- 소수 (측정값 등)
  'bool'       -- 예/아니오
);

-- 아이템 상태
CREATE TYPE item_status AS ENUM (
  'active',    -- 거래중
  'reserved',  -- 예약중
  'sold',      -- 거래완료
  'hidden'     -- 숨김
);

-- =========================================
-- 2) 행정구역 테이블: sd / sgg / emd
-- =========================================

-- 시도 (서울특별시, 경기도 등)
CREATE TABLE sd (
  sd_id   SERIAL PRIMARY KEY,
  name    VARCHAR(50) NOT NULL UNIQUE
);

-- 시군구 (송파구, 안양시 등)
CREATE TABLE sgg (
  sgg_id  SERIAL PRIMARY KEY,
  sd_id   INT NOT NULL REFERENCES sd(sd_id) ON DELETE RESTRICT,
  name    VARCHAR(50) NOT NULL,

  CONSTRAINT uq_sgg_sd_name UNIQUE (sd_id, name)
);

-- 읍면동 (잠실동, 평촌동 등) - 기존 regions를 emd로 변경
CREATE TABLE emd (
  region_id SERIAL PRIMARY KEY,
  sgg_id    INT NOT NULL REFERENCES sgg(sgg_id) ON DELETE RESTRICT,
  name      VARCHAR(50) NOT NULL,

  CONSTRAINT uq_emd_sgg_name UNIQUE (sgg_id, name)
);

CREATE INDEX idx_sgg_sd_id ON sgg(sd_id);
CREATE INDEX idx_emd_sgg_id ON emd(sgg_id);

-- =========================================
-- 3) 카테고리: category
-- =========================================

CREATE TABLE category (
  category_id SERIAL PRIMARY KEY,
  name        VARCHAR(100) NOT NULL UNIQUE
);

-- =========================================
-- 4) 속성 정의: attributes / attribute_options
-- =========================================

CREATE TABLE attributes (
  attribute_id SERIAL PRIMARY KEY,
  code         VARCHAR(100) NOT NULL UNIQUE,
  label        VARCHAR(100) NOT NULL,
  datatype     attribute_datatype NOT NULL,
  unit         VARCHAR(50),
  description  TEXT
);

CREATE TABLE attribute_options (
  option_id     SERIAL PRIMARY KEY,
  attribute_id  INT NOT NULL REFERENCES attributes(attribute_id) ON DELETE CASCADE,
  value         VARCHAR(100) NOT NULL,
  sort_order    INT NOT NULL DEFAULT 0,

  CONSTRAINT uq_attribute_option UNIQUE (attribute_id, value)
);

CREATE INDEX idx_attribute_options_attribute_id ON attribute_options(attribute_id);

-- =========================================
-- 5) 카테고리별 사용 속성: category_attributes
-- =========================================

CREATE TABLE category_attributes (
  category_id    INT NOT NULL REFERENCES category(category_id) ON DELETE CASCADE,
  attribute_id   INT NOT NULL REFERENCES attributes(attribute_id) ON DELETE CASCADE,
  is_required    BOOLEAN NOT NULL DEFAULT FALSE,
  display_group  VARCHAR(100),
  sort_order     INT NOT NULL DEFAULT 0,

  PRIMARY KEY (category_id, attribute_id)
);

-- =========================================
-- 6) 아이템: items
-- =========================================

CREATE TABLE items (
  item_id      BIGSERIAL PRIMARY KEY,
  region_id    INT NOT NULL REFERENCES emd(region_id) ON DELETE RESTRICT,
  category_id  INT NOT NULL REFERENCES category(category_id) ON DELETE RESTRICT,

  title        VARCHAR(500) NOT NULL,
  price        INT NOT NULL,
  status       item_status NOT NULL DEFAULT 'active',
  url          TEXT NOT NULL,

  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_items_region_id ON items(region_id);
CREATE INDEX idx_items_category_id ON items(category_id);
CREATE INDEX idx_items_created_at ON items(created_at DESC);
CREATE INDEX idx_items_category_created_at ON items(category_id, created_at DESC);
CREATE INDEX idx_items_region_category_price ON items(region_id, category_id, price);

-- =========================================
-- 7) 아이템 속성 값: item_attribute_values (EAV)
-- =========================================

CREATE TABLE item_attribute_values (
  item_id        BIGINT NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
  attribute_id   INT NOT NULL REFERENCES attributes(attribute_id) ON DELETE CASCADE,

  option_id      INT REFERENCES attribute_options(option_id) ON DELETE SET NULL,

  value_text     TEXT,
  value_int      INT,
  value_decimal  NUMERIC(18,4),
  value_bool     BOOLEAN,

  PRIMARY KEY (item_id, attribute_id)
);

CREATE INDEX idx_iav_item_id ON item_attribute_values(item_id);
CREATE INDEX idx_iav_attribute_id ON item_attribute_values(attribute_id);
CREATE INDEX idx_iav_option_id ON item_attribute_values(option_id);

-- =========================================
-- 8) SKU (Stock Keeping Unit): sku
-- =========================================

CREATE TABLE sku (
  sku_id       BIGSERIAL PRIMARY KEY,
  category_id  INT NOT NULL REFERENCES category(category_id) ON DELETE RESTRICT,

  -- 속성 조합의 고유 식별자 (해시값 등)
  fingerprint  VARCHAR(255) NOT NULL UNIQUE
);

CREATE INDEX idx_sku_category_id ON sku(category_id);
CREATE INDEX idx_sku_fingerprint ON sku(fingerprint);

-- =========================================
-- 9) SKU 속성: sku_attribute
-- =========================================

CREATE TABLE sku_attribute (
  sku_id         BIGINT NOT NULL REFERENCES sku(sku_id) ON DELETE CASCADE,
  attribute_id   INT NOT NULL REFERENCES attributes(attribute_id) ON DELETE CASCADE,

  option_id      INT REFERENCES attribute_options(option_id) ON DELETE SET NULL,

  value_text     TEXT,
  value_int      INT,
  value_decimal  NUMERIC(18,4),
  value_bool     BOOLEAN,

  PRIMARY KEY (sku_id, attribute_id)
);

CREATE INDEX idx_sku_attr_sku_id ON sku_attribute(sku_id);
CREATE INDEX idx_sku_attr_attribute_id ON sku_attribute(attribute_id);
CREATE INDEX idx_sku_attr_option_id ON sku_attribute(option_id);

-- =========================================
-- 10) 가격 통계: price_stats
-- =========================================

CREATE TABLE price_stats (
  sku_id       BIGINT NOT NULL REFERENCES sku(sku_id) ON DELETE CASCADE,
  region_id    INT REFERENCES emd(region_id) ON DELETE SET NULL,

  -- 시간 버킷 (일별, 주별 등)
  bucket_ts    TIMESTAMPTZ NOT NULL,

  -- 통계 정보
  items_num    INT NOT NULL DEFAULT 0,
  sum_price    BIGINT NOT NULL DEFAULT 0,
  avg_price    NUMERIC(18,2),
  min_price    INT,
  max_price    INT,

  PRIMARY KEY (sku_id, region_id, bucket_ts)
);

CREATE INDEX idx_price_stats_sku_id ON price_stats(sku_id);
CREATE INDEX idx_price_stats_region_id ON price_stats(region_id);
CREATE INDEX idx_price_stats_bucket_ts ON price_stats(bucket_ts DESC);
CREATE INDEX idx_price_stats_sku_region_bucket ON price_stats(sku_id, region_id, bucket_ts DESC);

-- =========================================
-- 트리거: updated_at 자동 업데이트
-- =========================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_items_updated_at
BEFORE UPDATE ON items
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();
