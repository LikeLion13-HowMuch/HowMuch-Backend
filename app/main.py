from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.analytics import router as analytics_router
from contextlib import asynccontextmanager
from app.core.scheduler import start_scheduler, shutdown_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()

app = FastAPI(
    title="HowMuch API",
    description="중고 애플 제품 시세 조회 API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://howmuchapple.store",
    "https://howmuchapple.store",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,     # 프론트엔드 주소
    allow_credentials=True,
    allow_methods=["*"],       # GET, POST, OPTIONS 등 모두 허용
    allow_headers=["*"],       # Authorization 등 헤더 허용
)

# 라우터 등록
app.include_router(analytics_router, prefix="/api/v1")

