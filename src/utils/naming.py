"""车间命名工具。

从 URL / 本地路径 / YouTube-DL 视频信息中提取人友好的车间名。

策略：

* 本地路径 → ``Path(src).stem``，去掉扩展名
* URL（含 youtube.com / youtu.be / bilibili.com） → 提示调用方应该先把视频信息解
  析出来再调 :py:func:`suggest_workshop_name_from_title`；这里只做基础清理
* 标题清理：移除 ``(Official Music Video)``、``【MV】``、``[HD]`` 等

边缘：

* 名字太长（>120 字符）截断
* 控制字符 / 不可见字符替换为 ``_``
* 全是空白或空字符串 → 返回 ``"New Workshop"``
"""

from __future__ import annotations

import re
from pathlib import Path

from src.kernel.core.workshop import DEFAULT_WORKSHOP_NAME

#: 标题清理规则：常见音乐平台附带的标签都拿掉（支持半角+全角括号）。
#: 拆成两个分开的正则分别处理半角与全角，避免字符类内部 escape 顺序歧义。
_TITLE_TAGS_HALF = re.compile(
    r"\s*[\(\[\{][^\)\]\}]*?(official|music video|高清|mv|hd|lyric|歌词|视频|video)[\)\]\}]",
    re.IGNORECASE,
)
_TITLE_TAGS_FULL = re.compile(
    r"\s*【[^】]*?(?:official|music video|高清|mv|hd|lyric|歌词|视频|video)[^】]*?】",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")

#: YouTube/Bilibili URL 模式（用于判断 URL 类型）
_YOUTUBE_DOMAINS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
_BILIBILI_DOMAINS = ("bilibili.com", "b23.tv")


def is_url(source: str) -> bool:
    """是否为网络 URL（简单启发式，不处理全部 edge case）。"""
    s = source.strip().lower()
    return s.startswith(("http://", "https://", "www."))


def is_youtube_url(url: str) -> bool:
    return any(d in url.lower() for d in _YOUTUBE_DOMAINS)


def is_bilibili_url(url: str) -> bool:
    return any(d in url.lower() for d in _BILIBILI_DOMAINS)


def sanitize_title(raw: str) -> str:
    """清理原始字符串作为车间名。

    规则：

    * 去首尾空白
    * 合并连续空白为单空格
    * 去掉 ``(Official Music Video)`` 之类标签
    * 替换控制字符为 ``_``
    * 截断到 120 字符
    """
    if not raw:
        return DEFAULT_WORKSHOP_NAME
    s = raw.strip()
    # 分别对半角与全角括号做去标签
    s = _TITLE_TAGS_HALF.sub("", s)
    s = _TITLE_TAGS_FULL.sub("", s)
    s = _WHITESPACE.sub(" ", s)
    # 控制字符 / 不可见字符 → 下划线
    s = "".join(c if c.isprintable() else "_" for c in s)
    s = s.strip()
    if len(s) > 120:
        s = s[:120].rstrip()
    return s or DEFAULT_WORKSHOP_NAME


def suggest_workshop_name_from_path(path: str | Path) -> str:
    """从本地路径/文件名提取车间名（去扩展名 + 清理）。"""
    p = Path(path)
    # 用 .stem 而不是 .name（保留多扩展名 .tar.gz）
    return sanitize_title(p.stem)


def suggest_workshop_name_from_title(title: str) -> str:
    """从视频/歌曲标题字符串提取车间名（仅清理）。"""
    return sanitize_title(title)


def suggest_workshop_name(source: str | Path | None) -> str:
    """统一入口：传入 URL / 本地路径 / 标题，自动选择策略。

    Args:
        source: URL 字符串、本地路径、或者已经解析出来的标题字符串。
            ``None`` 返回默认名。
    """
    if source is None:
        return DEFAULT_WORKSHOP_NAME
    s = str(source).strip()
    if not s:
        return DEFAULT_WORKSHOP_NAME
    if is_url(s):
        # MVP 阶段不在此调 yt-dlp 解析；让调用方先解析好后调
        # suggest_workshop_name_from_title。这里返回 URL 末段作为兜底。
        # 取 URL 最后一段去掉 query
        tail = s.split("?")[0].rstrip("/").split("/")[-1]
        return sanitize_title(tail)
    return suggest_workshop_name_from_path(s)


__all__ = [
    "is_url",
    "is_youtube_url",
    "is_bilibili_url",
    "sanitize_title",
    "suggest_workshop_name_from_path",
    "suggest_workshop_name_from_title",
    "suggest_workshop_name",
]
