"""
Crawler Task
기존 crawl_jg.py 실행
"""
import subprocess
import os


def run_crawler():
    """
    기존 crawl_jg.py 실행

    실행 명령:
    python crawl_jg.py -l 100 --save-db

    옵션:
    - -l 100: 최대 100개 페이지 크롤링
    - --save-db: DB에 저장
    """
    # 프로젝트 루트 디렉토리
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # crawl_jg.py 경로
    crawler_script = os.path.join(project_root, "crawl_jg.py")

    if not os.path.exists(crawler_script):
        print(f"❌ 크롤러 스크립트를 찾을 수 없습니다: {crawler_script}")
        return

    # 크롤링 실행
    result = subprocess.run(
        ["python", crawler_script, "-l", "100", "--save-db"],
        capture_output=True,
        text=True,
        cwd=project_root
    )

    if result.returncode != 0:
        print(f"❌ 크롤링 실패:\n{result.stderr}")
        raise Exception(f"Crawler failed: {result.stderr}")
    else:
        print(f"✅ 크롤링 성공:\n{result.stdout}")
