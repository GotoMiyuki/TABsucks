"""src/utils/naming.py 单元测试。"""

from __future__ import annotations

from src.kernel.core.workshop import DEFAULT_WORKSHOP_NAME
from src.utils.naming import (
    is_bilibili_url,
    is_url,
    is_youtube_url,
    sanitize_title,
    suggest_workshop_name,
    suggest_workshop_name_from_path,
    suggest_workshop_name_from_title,
)


class TestSanitize:
    def test_strip_whitespace(self) -> None:
        assert sanitize_title("  hello  ") == "hello"

    def test_empty_returns_default(self) -> None:
        assert sanitize_title("") == DEFAULT_WORKSHOP_NAME
        assert sanitize_title("   ") == DEFAULT_WORKSHOP_NAME

    def test_remove_official_mv_tag(self) -> None:
        assert sanitize_title("晴天 (Official Music Video)") == "晴天"

    def test_remove_lyric_tag_chinese(self) -> None:
        assert sanitize_title("【歌词版】消愁") == "消愁"

    def test_remove_hd_tag(self) -> None:
        assert sanitize_title("Song [HD]") == "Song"

    def test_collapse_spaces(self) -> None:
        assert sanitize_title("a    b   c") == "a b c"

    def test_truncate_long(self) -> None:
        long = "a" * 200
        out = sanitize_title(long)
        assert len(out) == 120

    def test_control_chars_replaced(self) -> None:
        assert sanitize_title("a\x00b\x01c") == "a_b_c"


class TestFromPath:
    def test_simple(self) -> None:
        assert suggest_workshop_name_from_path("/music/sunset.mp3") == "sunset"

    def test_no_extension_dotfile(self) -> None:
        assert suggest_workshop_name_from_path("/music/README") == "README"

    def test_multi_extension(self) -> None:
        # tar.gz 应取到 "archive.tar"
        assert (
            suggest_workshop_name_from_path("/x/archive.tar.gz") == "archive.tar"
        )

    def test_relative_path(self) -> None:
        assert suggest_workshop_name_from_path("./song.flac") == "song"


class TestFromTitle:
    def test_clean(self) -> None:
        assert suggest_workshop_name_from_title("Bohemian Rhapsody") == (
            "Bohemian Rhapsody"
        )

    def test_dirty(self) -> None:
        assert (
            suggest_workshop_name_from_title(
                "周杰伦 - 晴天 (Official MV) 4K"
            )
            == "周杰伦 - 晴天 4K"
        )


class TestIsUrl:
    def test_youtube(self) -> None:
        assert is_url("https://www.youtube.com/watch?v=xxx")
        assert is_youtube_url("https://youtu.be/xxx")

    def test_bilibili(self) -> None:
        assert is_bilibili_url("https://www.bilibili.com/video/BVxxx")

    def test_local_path(self) -> None:
        assert not is_url("/music/song.mp3")
        assert not is_url("C:\\music\\song.mp3")


class TestSuggestName:
    def test_none_returns_default(self) -> None:
        assert suggest_workshop_name(None) == DEFAULT_WORKSHOP_NAME
        assert suggest_workshop_name("") == DEFAULT_WORKSHOP_NAME

    def test_url_returns_tail(self) -> None:
        # MVP 不调 yt-dlp；用 URL 末段作为兜底
        out = suggest_workshop_name("https://www.youtube.com/watch?v=abc123")
        assert "abc123" in out or "watch" in out.lower()

    def test_local_path(self) -> None:
        assert suggest_workshop_name("/music/sunset.mp3") == "sunset"
