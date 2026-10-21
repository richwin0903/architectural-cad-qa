"""Non-sensitive status and a synthetic fixture for the local CAD QA prototype."""

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from cad_dxf_agent.core.cjk_font import cjk_font_path

from .production_dwg import dwg_executable

router = APIRouter()
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@router.get("/api/local-status")
async def local_status():
    return {
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        "configured": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
        "dwg_available": dwg_executable() is not None,
        "dwg_converter": "LibreDWG" if dwg_executable() is not None else None,
        "mode": "read_only",
        "data_notice": "提问时，相关图纸文字、图元属性和工具结果会发送至 DeepSeek API。",
    }


@router.get("/api/local-demo")
async def local_demo():
    path = PROJECT_ROOT / "local_examples" / "architectural-demo.dxf"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="示例图纸尚未生成。")
    return FileResponse(path, media_type="application/dxf", filename=path.name)


@router.get("/api/local-font")
async def local_font():
    """Serve the packaged OFL font under the application's normal API authentication."""
    font_path = cjk_font_path()
    if not font_path.is_file():
        raise HTTPException(status_code=404, detail="服务器未配置可用的开源中文字体。")
    return FileResponse(
        font_path,
        media_type="font/ttf",
        headers={"Cache-Control": "private, max-age=86400"},
    )
