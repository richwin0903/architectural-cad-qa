"""The production image ships only the redistributable Noto Chinese font."""

from pathlib import Path

from ezdxf.fonts import fonts
from ezdxf.fonts.font_manager import FontManager

from cad_dxf_agent.core.cjk_font import register_cjk_font


def test_packaged_font_handles_missing_inline_chinese_family(monkeypatch):
    # A developer laptop has system fallback fonts and masks this Linux-only
    # failure. Exercise an empty cache, matching the slim production image.
    manager = FontManager()
    monkeypatch.setattr(fonts, "font_manager", manager)
    font = Path(__file__).resolve().parents[2] / "fonts" / "NotoSansSC-Regular.ttf"
    register_cjk_font.cache_clear()
    try:
        assert register_cjk_font(font) == font.name
        assert manager.get_font_face("unavailable-inline-font.ttf").filename == font.name
        assert fonts.find_font_file_name(fonts.FontFace(family="宋体")) == font.name
        assert manager.get_ttf_font(font.name).getBestCmap()[ord("房")]
    finally:
        register_cjk_font.cache_clear()
