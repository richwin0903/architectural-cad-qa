"""Production entry point: only the read-only CAD QA surface is mounted.

Run one Uvicorn worker. The existing prototype's FastAPI application, lifespan,
Firebase auth, editing APIs, debug APIs and CORS middleware are never served.
The reverse proxy strips CAD_PUBLIC_PREFIX and enforces TLS/body limits.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .admin_auth import AdminAuth, AuthConfig, public_session
from .production_dwg import normalized_dwg


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


def browser_dxf_encoding(encoding: str) -> str:
    """Map Python CAD codepage names to browser TextDecoder labels."""
    encoding = encoding.lower().replace("_", "-")
    aliases = {
        "utf8": "utf-8",
        "cp936": "gbk",
        "gb2312": "gbk",
        "cp950": "big5",
        "cp932": "shift_jis",
        "shift-jis": "shift_jis",
        "cp949": "euc-kr",
        "cp874": "windows-874",
    }
    if re.fullmatch(r"cp125[0-8]", encoding):
        return "windows-" + encoding[2:]
    return aliases.get(encoding, encoding)


def parse_readonly_upload(file, user, session_manager, *, max_bytes, render_limit):
    """Parse in a worker without editing, reserializing, or rewriting the DXF.

    Native converters can emit valid read-only geometry with metadata that an
    editor cannot round-trip. The QA service needs the source bytes and parsed
    context only, so it never initializes EditHistory or a working DXF copy.
    """
    from ezdxf.filemanagement import dxf_file_info

    from cad_dxf_agent.core.dxf_reader import load_dxf
    from cad_dxf_agent.core.renderer import render_dxf_to_png

    data = file.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, "图纸最大为 25 MB。")
    drawing = session_manager.create(user["uid"])
    drawing.original_path.write_bytes(data)
    try:
        header = dxf_file_info(drawing.original_path)
        context = load_dxf(drawing.original_path)
    except Exception:
        raise HTTPException(422, "图纸无法解析，请检查 DXF 文件是否完整。") from None
    drawing.context = context
    layers = sorted({entity.layer for entity in context.entities})
    drawing.file_info = {
        "filename": file.filename,
        "entity_count": context.entity_count,
        "layer_count": len(layers),
        "layers": layers,
        "dxf_encoding": browser_dxf_encoding(header.encoding),
        "dxf_version": header.version,
    }
    if context.entity_count <= render_limit:
        try:
            preview = render_dxf_to_png(
                drawing.original_path, drawing.original_path.parent / "original.png"
            )
            if preview.success:
                drawing.original_render = preview.output_path
        except Exception:
            logging.getLogger(__name__).warning("Drawing static preview is unavailable")
    return {"session_id": drawing.session_id, "file_info": drawing.file_info}


def create_app(
    config: AuthConfig | None = None, *, session_cleanup_dir: Path | None = None
) -> FastAPI:
    from . import main as prototype

    # main imports dotenv through settings; validate *after* that import, too.
    if os.getenv("CAD_WEB_DEV_MODE", "").strip().lower() not in {"0", "false", "no"}:
        raise RuntimeError("Production requires explicit CAD_WEB_DEV_MODE=false")
    auth = AdminAuth(config or AuthConfig.from_env())
    prefix = auth.config.prefix
    frontend = Path(
        os.getenv("CAD_FRONTEND_DIST", str(prototype.PROJECT_ROOT / "web/frontend/dist"))
    )
    session_dir = session_cleanup_dir or Path("/tmp/cad-sessions")
    upload_lock = asyncio.Lock()

    def delete_drawings(uid: str) -> None:
        for session in list(prototype.session_mgr._sessions.values()):
            if session.user_id == uid:
                prototype.session_mgr.delete(session.session_id)

    def cleanup(*, startup: bool = False) -> None:
        # Capture drawings first. A login/upload that arrives after this
        # snapshot cannot be mistaken for a revoked older session.
        drawings = list(prototype.session_mgr._sessions.values())
        active = auth.active_uids()
        for session in drawings:
            if session.user_id not in active:
                prototype.session_mgr.delete(session.session_id)
        prototype.session_mgr.cleanup_expired()
        # Uploaded drawings are intentionally transient. Also remove directory
        # leftovers after a failed upload or process restart (no runtime state).
        known = set(prototype.session_mgr._sessions)
        if session_dir.exists():
            for item in session_dir.iterdir():
                if re.fullmatch(r"[0-9a-f]{16}", item.name) and item.name not in known:
                    if item.is_symlink():
                        item.unlink()
                    elif item.is_dir() and (startup or item.stat().st_mtime < time.time() - 900):
                        shutil.rmtree(item)

    async def cleanup_loop():
        while True:
            await asyncio.sleep(300)
            try:
                await run_in_threadpool(cleanup)
            except OSError:
                # Do not log file names or contents; retry at the next interval.
                logging.getLogger(__name__).warning("Temporary drawing cleanup will be retried")

    @asynccontextmanager
    async def lifespan(_app):
        cleanup(startup=True)
        task = asyncio.create_task(cleanup_loop())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="建筑 CAD 图纸问答",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.admin_auth = auth
    app.state.cleanup = cleanup

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exc: RequestValidationError):
        # FastAPI's default validation payload echoes invalid input, which may
        # contain a password or question, and cannot encode lone surrogates.
        return JSONResponse({"detail": "请求参数无效，请检查输入。"}, status_code=422)

    async def require_user(request: Request) -> dict:
        user = getattr(request.state, "admin", None)
        if user is None:
            raise HTTPException(401, "请先登录。")
        return user

    # Reused APIRoute dependency objects point to these original callables.
    # Override both, so no path ever reaches Firebase or CAD_WEB_DEV_MODE.
    app.dependency_overrides[prototype.get_user] = require_user
    app.dependency_overrides[prototype.get_licensed_user] = require_user

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        path = request.scope.get("path", request.url.path)
        # root_path is used in tests/ASGI proxies; the actual Caddy upstream
        # sends stripped paths and does not set a root_path.
        root_path = request.scope.get("root_path", "")
        if root_path and path.startswith(root_path + "/"):
            path = path[len(root_path) :]
        request.state.admin = auth.authenticate(request)
        public = path in {"/api/health", "/api/auth/login"}
        if path.startswith("/api/") and not public:
            if request.state.admin is None:
                response = JSONResponse({"detail": "登录已过期，请重新登录。"}, status_code=401)
                auth.clear_cookie(response)
            else:
                try:
                    if request.method not in {"GET", "HEAD", "OPTIONS"}:
                        auth.require_csrf(request, request.state.admin)
                    response = await call_next(request)
                except HTTPException as exc:
                    response = JSONResponse(
                        {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
                    )
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        if path.startswith("/api/") or path in {"/", "/login", "/local-qa", "/index.html"}:
            response.headers["Cache-Control"] = "no-store"
        if response.status_code == 401:
            auth.clear_cookie(response)
        return response

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.post("/api/auth/login")
    async def login(body: LoginBody, request: Request):
        auth.require_origin(request)
        # Limit by actual peer, never by a spoofable X-Forwarded-For header.
        peer = request.client.host if request.client else "unknown"
        token, user = await run_in_threadpool(auth.login, body.username, body.password, peer)
        old_user = request.state.admin
        if old_user:
            auth.revoke(old_user["uid"])
            delete_drawings(old_user["uid"])
        response = JSONResponse(public_session(user))
        auth.set_cookie(response, token)
        return response

    @app.get("/api/auth/session")
    async def session(user: dict = Depends(require_user)):
        return public_session(user)

    @app.post("/api/auth/logout")
    async def logout(user: dict = Depends(require_user)):
        auth.revoke(user["uid"])
        delete_drawings(user["uid"])
        response = JSONResponse({"ok": True})
        auth.clear_cookie(response)
        return response

    # This is an allowlist, never a mount of the upstream application. New
    # upstream routes are closed unless deliberately added here.
    allowed = {
        ("POST", "/api/local-qa"),
        ("POST", "/api/entities/near"),
        ("GET", "/api/dxf"),
        ("GET", "/api/render"),
        ("GET", "/api/local-status"),
        ("GET", "/api/local-demo"),
        ("GET", "/api/local-font"),
    }
    copied = set()
    # Recent FastAPI versions preserve included routers as lazy wrappers. Read
    # the two explicit QA/support routers rather than relying on flattening.
    sources = [
        prototype.app,
        prototype.create_local_qa_router(prototype.session_mgr, prototype.get_user),
        prototype.local_support_router,
    ]
    for source in sources:
        for route in source.routes:
            if not isinstance(route, APIRoute):
                continue
            selected = {(method, route.path) for method in route.methods} & allowed
            if selected - copied:
                # add_api_route recreates dependencies using this app's provider.
                app.add_api_route(
                    route.path,
                    route.endpoint,
                    methods=sorted(method for method, _ in selected),
                    response_model=route.response_model,
                    name=route.name,
                )
                copied |= selected
    if copied != allowed:
        raise RuntimeError("A required read-only upstream route is missing")

    @app.post("/api/upload")
    async def upload(
        request: Request, file: UploadFile = File(...), user: dict = Depends(require_user)
    ):
        ext = Path(file.filename or "").suffix.lower()
        if ext not in {".dxf", ".dwg"}:
            raise HTTPException(400, "仅支持 DXF 或 DWG 图纸。")

        # Read at most 25 MB + 1 rather than unbounded data into memory. The
        # proxy additionally caps the complete multipart body before parsing.
        def require_live_login():
            current = auth.authenticate(request)
            if current is None or current["uid"] != user["uid"]:
                raise HTTPException(401, "登录已过期，请重新登录。")

        async with upload_lock:
            require_live_login()
            data = await file.read(prototype.MAX_UPLOAD_SIZE + 1)
            if len(data) > prototype.MAX_UPLOAD_SIZE:
                raise HTTPException(413, "图纸最大为 25 MB。")
            require_live_login()
            before = set(prototype.session_mgr._sessions)

            def parse_in_worker(upload_file):
                return parse_readonly_upload(
                    upload_file,
                    user,
                    prototype.session_mgr,
                    max_bytes=prototype.MAX_UPLOAD_SIZE,
                    render_limit=prototype.RENDER_ENTITY_LIMIT,
                )

            try:
                if ext == ".dwg":
                    async with normalized_dwg(data) as dxf_path:
                        del data
                        require_live_login()
                        with dxf_path.open("rb") as stream:
                            normalized = UploadFile(
                                file=stream, filename="drawing.dxf", size=dxf_path.stat().st_size
                            )
                            response = await run_in_threadpool(parse_in_worker, normalized)
                else:
                    del data
                    await file.seek(0)
                    require_live_login()
                    response = await run_in_threadpool(parse_in_worker, file)
                require_live_login()
                drawing = prototype.session_mgr.get(response["session_id"], user["uid"])
                drawing.file_info.update(
                    filename=Path(file.filename or "drawing" + ext).name,
                    source_format=ext[1:],
                    normalized_format="dxf",
                )
                if ext == ".dwg":
                    drawing.file_info["converter"] = "LibreDWG"
                    drawing.file_info["conversion_notice"] = (
                        "DWG 已在服务器通过 LibreDWG 转为 DXF；代理对象、外部参照及部分高级对象"
                        "可能丢失或不受支持，请与原始图纸人工核对。"
                    )
                prototype.session_mgr.save_metadata(drawing)
                response["file_info"] = drawing.file_info
                return response
            except BaseException as error:
                for sid, drawing in list(prototype.session_mgr._sessions.items()):
                    if sid not in before and drawing.user_id == user["uid"]:
                        prototype.session_mgr.delete(sid)
                if isinstance(error, Exception):
                    # Logout can delete a drawing while a worker is parsing.
                    # Treat that outcome as revoked authentication, not a 500.
                    require_live_login()
                if (
                    ext == ".dwg"
                    and isinstance(error, HTTPException)
                    and error.status_code == 422
                    and error.detail == "图纸无法解析，请检查 DXF 文件是否完整。"
                ):
                    raise HTTPException(
                        422, "DWG 转换后的图纸无法解析，请检查文件或另存为 DXF。"
                    ) from None
                raise

    @app.get("/api/download")
    async def download(session_id: str, user: dict = Depends(require_user)):
        try:
            drawing = prototype.session_mgr.get(session_id, user["uid"])
        except (KeyError, PermissionError):
            raise HTTPException(404, "图纸不存在或已过期。") from None
        return FileResponse(
            drawing.original_path, media_type="application/dxf", filename="drawing.dxf"
        )

    def frontend_response():
        path = frontend / "index.html"
        if not path.is_file():
            raise HTTPException(503, "前端尚未构建。")
        return FileResponse(path, media_type="text/html")

    @app.get("/login")
    async def login_page():
        return frontend_response()

    @app.get("/local-qa")
    async def qa_page(request: Request):
        if not request.state.admin:
            return RedirectResponse(prefix + "/login", status_code=303)
        return frontend_response()

    @app.get("/")
    @app.get("/index.html")
    async def index(request: Request):
        target = "/local-qa" if request.state.admin else "/login"
        return RedirectResponse(prefix + target, status_code=303)

    if (frontend / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")
    if (frontend / "fonts").is_dir():
        app.mount("/fonts", StaticFiles(directory=frontend / "fonts"), name="fonts")
    return app


app = create_app()
