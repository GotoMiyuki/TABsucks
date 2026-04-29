"""工具函数测试。"""

from __future__ import annotations

import pytest

from src.utils.helpers import clamp, db_to_linear, format_time, linear_to_db


class TestFormatTime:
    """format_time 函数测试。"""

    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0, "0:00"),
            (5, "0:05"),
            (30, "0:30"),
            (60, "1:00"),
            (90, "1:30"),
            (3600, "60:00"),
            (0.5, "0:00"),  # 截断到秒
            (59.9, "0:59"),
        ],
    )
    def test_format_time(self, seconds: float, expected: str) -> None:
        """格式化结果符合预期。"""
        assert format_time(seconds) == expected

    def test_negative_time_clamped(self) -> None:
        """负数被限制为 0:00。"""
        assert format_time(-10.0) == "0:00"


class TestClamp:
    """clamp 函数测试。"""

    def test_within_range(self) -> None:
        """值在范围内时不变。"""
        assert clamp(0.5, 0.0, 1.0) == 0.5

    def test_below_min(self) -> None:
        """小于最小值时返回最小值。"""
        assert clamp(-0.5, 0.0, 1.0) == 0.0

    def test_above_max(self) -> None:
        """大于最大值时返回最大值。"""
        assert clamp(1.5, 0.0, 1.0) == 1.0

    def test_exact_min(self) -> None:
        """恰好等于最小值。"""
        assert clamp(0.0, 0.0, 1.0) == 0.0

    def test_exact_max(self) -> None:
        """恰好等于最大值。"""
        assert clamp(1.0, 0.0, 1.0) == 1.0


class TestDbConversions:
    """分贝与线性幅度转换测试。"""

    def test_db_to_linear_zero_db(self) -> None:
        """0 dB 对应线性幅度 1.0。"""
        assert db_to_linear(0.0) == pytest.approx(1.0, rel=1e-5)

    def test_db_to_linear_negative(self) -> None:
        """负 dB 值对应小于 1.0 的幅度。"""
        assert db_to_linear(-20.0) == pytest.approx(0.1, rel=1e-3)

    def test_linear_to_db_one(self) -> None:
        """线性幅度 1.0 对应 0 dB。"""
        assert linear_to_db(1.0) == pytest.approx(0.0, rel=1e-5)

    def test_linear_to_db_zero(self) -> None:
        """线性幅度 0 对应负无穷 dB。"""
        assert linear_to_db(0.0) == float("-inf")

    def test_roundtrip(self) -> None:
        """db_to_linear -> linear_to_db 应往返一致。"""
        original_db = -12.0
        linear = db_to_linear(original_db)
        result = linear_to_db(linear)
        assert result == pytest.approx(original_db, rel=1e-5)
