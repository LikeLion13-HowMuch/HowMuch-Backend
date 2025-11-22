"""
Utility Helper Functions
"""


def format_price(price: int) -> str:
    """
    가격을 천 단위 구분 형식으로 변환

    Args:
        price: 가격 (정수)

    Returns:
        포맷된 가격 문자열 (ex. 1,000,000원)
    """
    return f"{price:,}원"


def calculate_percentage_change(old_value: float, new_value: float) -> float:
    """
    퍼센트 변화율 계산

    Args:
        old_value: 이전 값
        new_value: 새로운 값

    Returns:
        변화율 (%)
    """
    if old_value == 0:
        return 0.0

    return ((new_value - old_value) / old_value) * 100


def truncate_text(text: str, max_length: int = 100) -> str:
    """
    텍스트를 최대 길이로 자르기

    Args:
        text: 원본 텍스트
        max_length: 최대 길이

    Returns:
        잘린 텍스트
    """
    if len(text) <= max_length:
        return text

    return text[:max_length] + "..."
