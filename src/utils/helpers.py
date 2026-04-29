"""通用辅助函数。"""

from __future__ import annotations


def format_time(seconds: float) -> str:
    """将秒数格式化为 `M:SS` 字符串。

    Args:
        seconds: 秒数。

    Returns:
        格式化后的时间字符串，如 "1:30"。

    Examples:
        >>> format_time(90)
        '1:30'
        >>> format_time(5)
        '0:05'
    """
    if seconds < 0:
        seconds = 0.0
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}:{secs:02d}"


def clamp(value: float, min_val: float, max_val: float) -> float:
    """将值限制在 [min_val, max_val] 范围内。

    Args:
        value: 输入值。
        min_val: 最小值。
        max_val: 最大值。

    Returns:
        限制后的值。

    Examples:
        >>> clamp(0.5, 0.0, 1.0)
        0.5
        >>> clamp(-0.5, 0.0, 1.0)
        0.0
        >>> clamp(1.5, 0.0, 1.0)
        1.0
    """
    if value < min_val:
        return min_val
    if value > max_val:
        return max_val
    return value


def db_to_linear(db: float) -> float:
    """将分贝值转换为线性幅度。

    Args:
        db: 分贝值（dB）。

    Returns:
        线性幅度值。
    """
    return 10 ** (db / 20.0)


def linear_to_db(linear: float) -> float:
    """将线性幅度转换为分贝值。

    Args:
        linear: 线性幅度值。

    Returns:
        分贝值（dB）。
    """
    import math

    if linear <= 0:
        return float("-inf")
    return 20.0 * math.log10(linear)
