"""
FastAPI Application Entry Point
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import products
from app.core.scheduler import start_scheduler

app = FastAPI(
    title="HowMuch API",
    description="중고 애플 제품 시세 조회 API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: 프론트엔드 도메인으로 제한 필요
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(products.router, prefix="/api/v1", tags=["products"])


@app.on_event("startup")
async def startup_event():
    """
    서버 시작 시 실행
    - 크롤링 스케줄러 시작
    """
    print("=" * 50)
    print("HowMuch API Server Starting...")
    print("=" * 50)

    # 스케줄러 시작
    start_scheduler()

    print("Server started successfully!")


@app.on_event("shutdown")
async def shutdown_event():
    """
    서버 종료 시 실행
    """
    print("=" * 50)
    print("HowMuch API Server Shutting Down...")
    print("=" * 50)


@app.get("/")
async def root():
    """
    루트 엔드포인트
    """
    return {
        "message": "HowMuch API is running",
        "version": "1.0.0",
        "docs": "/docs"
    }


@app.get("/health")
async def health_check():
    """
    헬스 체크 엔드포인트
    """
    return {"status": "healthy"}
