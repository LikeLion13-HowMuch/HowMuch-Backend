# 🚀 프론트엔드 팀을 위한 백엔드 가이드

## 📋 목차
1. [프로젝트 구조](#프로젝트-구조)
2. [환경 설정](#환경-설정)
3. [서버 실행](#서버-실행)
4. [API 사용법](#api-사용법)
5. [데이터베이스](#데이터베이스)
6. [문제 해결](#문제-해결)

---

## 📁 프로젝트 구조

```
HowMuch-Backend/
├── app/                    # FastAPI 애플리케이션
│   ├── main.py            # 메인 엔트리포인트
│   ├── models.py          # Request/Response 모델
│   ├── crud.py            # 데이터베이스 작업
│   └── routers/
│       └── products.py    # 제품 시세 API
├── tasks/                  # 백그라운드 작업
│   ├── scheduler.py       # 크롤링 스케줄러
│   ├── crawler.py         # 크롤러
│   └── sku_generator.py   # SKU 생성
├── crawl_jg.py            # 중고나라 크롤러 (수동 실행용)
├── generate_sku_and_stats.py  # SKU/통계 생성 (수동 실행용)
├── db_manager.py          # DB 유틸리티
├── schema_new.sql         # 최신 DB 스키마
└── .env                   # 환경 변수 (본인이 생성해야 함)
```

---

## 🔧 환경 설정

### 1️⃣ PostgreSQL 설치 및 실행

#### macOS
```bash
# Homebrew로 설치
brew install postgresql

# 서비스 시작
brew services start postgresql

# 또는 직접 실행
postgres -D /usr/local/var/postgres
```

#### Windows
- [PostgreSQL 공식 사이트](https://www.postgresql.org/download/)에서 설치
- pgAdmin 사용 가능

---

### 2️⃣ 데이터베이스 생성

```bash
# PostgreSQL 접속
psql postgres

# 데이터베이스 생성
CREATE DATABASE howmuch;

# 사용자 확인 (본인의 사용자명 사용)
\du

# 종료
\q
```

---

### 3️⃣ 스키마 적용

```bash
# 프로젝트 디렉토리에서
psql -U [사용자명] -d howmuch -f schema_new.sql

# 예시:
psql -U byunmingyu -d howmuch -f schema_new.sql
```

---

### 4️⃣ .env 파일 생성

프로젝트 루트에 `.env` 파일 생성:

```env
# PostgreSQL Database Configuration
DB_HOST=localhost
DB_PORT=5432
DB_NAME=howmuch
DB_USER=[본인의_사용자명]
DB_PASSWORD=

# FastAPI Database URL
DATABASE_URL=postgresql://[본인의_사용자명]:@localhost:5432/howmuch
```

**⚠️ 주의**: `[본인의_사용자명]`을 실제 PostgreSQL 사용자명으로 변경!

---

### 5️⃣ Python 패키지 설치

```bash
pip install -r requirements.txt
```

---

### 6️⃣ 초기 데이터 크롤링 (선택)

```bash
# 중고나라 크롤링 (100페이지)
python crawl_jg.py -l 100 --save-db

# SKU 및 통계 생성
python generate_sku_and_stats.py
```

**⚠️ 참고**: 크롤링 없이도 서버는 실행됩니다. 다만 데이터가 없어서 API 응답이 비어있을 수 있습니다.

---

## 🚀 서버 실행

### 개발 서버 시작
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 서버 확인
- **서버 주소**: http://localhost:8000
- **API 문서 (Swagger)**: http://localhost:8000/docs
- **헬스 체크**: http://localhost:8000/health

---

## 📡 API 사용법

### 🔵 메인 API: 제품 시세 조회

**Endpoint**: `POST /api/v1/products/price`

#### Request Body
```json
{
  "product": "iPhone",
  "spec": {
    "model": "아이폰 15 프로",
    "storage": "128GB",
    "color": "블루"
  },
  "region": {
    "sd": "서울특별시",
    "sgg": "강남구",
    "emd": "역삼동"
  }
}
```

#### Response (성공)
```json
{
  "status": "success",
  "data": {
    "summary_info": {
      "model_name": "아이폰 15 프로 128GB 블루",
      "average_price": 750000,
      "highest_listing_price": 800000,
      "lowest_listing_price": 700000,
      "listing_count": 5,
      "data_date": "2025-11-19 17:00"
    },
    "regional_analysis": {
      "detail_by_district": [
        {
          "emd": "역삼동",
          "average_price": 750000,
          "listing_count": 3
        },
        {
          "emd": "대치동",
          "average_price": 780000,
          "listing_count": 2
        }
      ]
    },
    "price_trend": {
      "trend_period": 7,
      "change_rate": -2.5,
      "chart_data": [
        {
          "period": "11월 1주",
          "price": 770000
        },
        {
          "period": "11월 2주",
          "price": 750000
        }
      ]
    },
    "lowest_price_listings": [
      {
        "listing_price": 700000,
        "district_detail": "강남구 역삼동",
        "source": "중고나라",
        "source_url": "https://web.joongna.com/product/123456"
      }
    ]
  },
  "message": null
}
```

#### Response (에러)
```json
{
  "status": "error",
  "data": null,
  "message": "해당 조건의 제품을 찾을 수 없습니다."
}
```

---

### 📝 Request 필드 상세 설명

#### `product` (필수)
- **타입**: string
- **가능한 값**: `"iPhone"`, `"AppleWatch"`, `"iPad"`
- ⚠️ **대소문자 정확히**: `"iphone"` ❌

#### `spec` (필수)
모든 필드는 **nullable** (선택적)

| 필드 | 타입 | 예시 | 주의사항 |
|-----|------|------|---------|
| `model` | string | `"아이폰 15 프로"` | 한글, 띄어쓰기 정확히 |
| `storage` | string | `"128GB"` | 대문자, 띄어쓰기 없음 |
| `color` | string | `"블루"` | 한글 |
| `chip` | string | `"M2"` | Mac 전용 |
| `ram` | string | `"16GB"` | Mac 전용 |
| `screen_size` | string | `"13-inch"` | Mac, iPad |
| `size` | string | `"49mm"` | AppleWatch 전용 |
| `material` | string | `"티타늄"` | AppleWatch 전용 |
| `connectivity` | string | `"GPS + 셀룰러"` | AppleWatch 전용 |
| `cellular` | string | `"Wi-Fi + Cellular"` | iPad 전용 |
| `pencil_support` | boolean | `true` | iPad 전용 |

#### `region` (선택)
모든 필드는 **nullable**

```json
// 전국 검색
"region": {}

// 시도만
"region": {
  "sd": "서울특별시"
}

// 시군구까지
"region": {
  "sd": "서울특별시",
  "sgg": "강남구"
}

// 읍면동까지 (상세)
"region": {
  "sd": "서울특별시",
  "sgg": "강남구",
  "emd": "역삼동"
}
```

---

### 🧪 테스트 예시

#### JavaScript (Fetch)
```javascript
const response = await fetch('http://localhost:8000/api/v1/products/price', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    product: 'iPhone',
    spec: {
      model: '아이폰 15 프로',
      storage: '128GB',
      color: '블루'
    },
    region: {}
  })
});

const data = await response.json();
console.log(data);
```

#### axios
```javascript
import axios from 'axios';

const { data } = await axios.post('http://localhost:8000/api/v1/products/price', {
  product: 'iPhone',
  spec: {
    model: '아이폰 15 프로',
    storage: '128GB',
    color: '블루'
  },
  region: {}
});

console.log(data);
```

#### curl
```bash
curl -X POST http://localhost:8000/api/v1/products/price \
  -H "Content-Type: application/json" \
  -d '{
    "product": "iPhone",
    "spec": {
      "model": "아이폰 15 프로",
      "storage": "128GB",
      "color": "블루"
    },
    "region": {}
  }'
```

---

## 🗄️ 데이터베이스

### DB 확인 명령어

```bash
# PostgreSQL 접속
psql -U [사용자명] -d howmuch

# 테이블 목록
\dt

# 특정 테이블 구조 확인
\d items
\d sku
\d price_stats

# 데이터 개수 확인
SELECT COUNT(*) FROM items;
SELECT COUNT(*) FROM sku;

# 종료
\q
```

### 주요 테이블

| 테이블 | 설명 | 예시 쿼리 |
|-------|------|----------|
| `items` | 크롤링된 매물 | `SELECT * FROM items LIMIT 10;` |
| `sku` | 제품 스펙 조합 | `SELECT * FROM sku LIMIT 10;` |
| `price_stats` | 가격 통계 | `SELECT * FROM price_stats LIMIT 10;` |
| `category` | 제품 카테고리 | `SELECT * FROM category;` |

---

## ❓ 문제 해결

### 🔴 서버가 안 켜져요

#### 1. PostgreSQL이 실행 중인지 확인
```bash
# macOS
brew services list | grep postgresql

# 실행
brew services start postgresql
```

#### 2. .env 파일 확인
```bash
cat .env
```
- `DATABASE_URL` 확인
- 사용자명이 맞는지 확인

#### 3. DB 연결 테스트
```bash
psql -U [사용자명] -d howmuch -c "SELECT 1;"
```

---

### 🟡 API가 빈 응답을 줘요

#### 원인
- DB에 데이터가 없음
- SKU가 생성되지 않음

#### 해결
```bash
# 1. 크롤링
python crawl_jg.py -l 10 --save-db

# 2. SKU 생성
python generate_sku_and_stats.py

# 3. 확인
psql -U [사용자명] -d howmuch -c "SELECT COUNT(*) FROM items;"
psql -U [사용자명] -d howmuch -c "SELECT COUNT(*) FROM sku;"
```

---

### 🟢 모델명을 모르겠어요

#### DB에서 확인
```bash
psql -U [사용자명] -d howmuch -c "
SELECT DISTINCT value_text as model, COUNT(*) as count
FROM item_attribute_values iav
JOIN attributes a ON iav.attribute_id = a.attribute_id
WHERE a.code = 'model'
GROUP BY value_text
ORDER BY count DESC
LIMIT 20;
"
```

---

## 📚 추가 참고 자료

- **API 문서**: http://localhost:8000/docs (서버 실행 후)
- **DB 스키마**: `schema_new.sql` 파일 참고
- **백엔드 코드**: `app/` 디렉토리

---

## 🆘 도움이 필요하면?

1. **API 문서 확인**: http://localhost:8000/docs
2. **로그 확인**: 서버 실행 터미널 로그
3. **DB 상태 확인**: `psql` 명령어 사용
4. **백엔드 팀에 문의**: [담당자 연락처]

---

## ✅ 체크리스트

프론트엔드 개발 시작 전 확인:

- [ ] PostgreSQL 설치 및 실행
- [ ] `howmuch` 데이터베이스 생성
- [ ] 스키마 적용 (`schema_new.sql`)
- [ ] `.env` 파일 생성 및 설정
- [ ] Python 패키지 설치 (`pip install -r requirements.txt`)
- [ ] 서버 실행 (`uvicorn app.main:app --reload`)
- [ ] API 문서 접속 확인 (http://localhost:8000/docs)
- [ ] 테스트 요청 성공

모든 항목이 완료되면 개발 시작! 🎉
