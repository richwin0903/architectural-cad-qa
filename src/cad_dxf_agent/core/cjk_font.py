"""Shared, redistributable Chinese font used by browser and static CAD previews."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def cjk_font_path() -> Path:
    """Resolve the packaged OFL font; never depend on a host OS font directory."""
    default = Path(__file__).resolve().parents[3] / "fonts" / "NotoSansSC-Regular.ttf"
    return Path(os.environ.get("CAD_CJK_FONT", str(default))).expanduser().resolve()


@lru_cache(maxsize=4)
def register_cjk_font(path: Path) -> str:
    """Register the exact same TTF served to dxf-viewer with ezdxf's font manager."""
    if not path.is_file():
        raise FileNotFoundError("Configured CAD Chinese font is unavailable")
    from ezdxf.fonts import fonts
    from ezdxf.fonts.font_manager import DEFAULT_FONTS

    fonts.font_manager.scan_folder(path.parent)
    if not fonts.font_manager.has_font(path.name):
        raise RuntimeError("Configured CAD Chinese font could not be loaded")
    # MTEXT may request an unavailable inline family (for example 宋体).
    # ezdxf's built-in fallback list does not include Noto Sans SC, so an
    # otherwise valid font cache with only this packaged font has no fallback.
    # Register aliases through its public API; no OS font files are required.
    for fallback in DEFAULT_FONTS:
        fonts.font_manager.add_synonyms({path.name: fallback}, reverse=False)
    return path.name
