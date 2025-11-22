"""
SKU Generator Task
기존 generate_sku_and_stats.py 실행
"""
import subprocess
import os


def run_sku_generation():
    """
    기존 generate_sku_and_stats.py 실행

    실행 명령:
    python generate_sku_and_stats.py

    기능:
    - SKU 생성 (fingerprint 기반)
    - 가격 통계 업데이트
    """
    # 프로젝트 루트 디렉토리
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # generate_sku_and_stats.py 경로
    sku_script = os.path.join(project_root, "generate_sku_and_stats.py")

    if not os.path.exists(sku_script):
        print(f"❌ SKU 생성 스크립트를 찾을 수 없습니다: {sku_script}")
        return

    # SKU 생성 실행
    result = subprocess.run(
        ["python", sku_script],
        capture_output=True,
        text=True,
        cwd=project_root
    )

    if result.returncode != 0:
        print(f"❌ SKU 생성 실패:\n{result.stderr}")
        raise Exception(f"SKU generation failed: {result.stderr}")
    else:
        print(f"✅ SKU 생성 성공:\n{result.stdout}")
