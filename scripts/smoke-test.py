#!/usr/bin/env python3
"""Verify a running HTTPS deployment with bundled synthetic drawings, without LLM calls."""

from __future__ import annotations

import argparse
import getpass
import io
import math
import re
import ssl
import sys
import tempfile
import warnings
from contextlib import suppress
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import ezdxf
import httpx
from ezdxf.units import conversion_factor
from PIL import Image, ImageOps

COOKIE_NAME = "cad_admin_session"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SmokeError(Exception):
    """A short diagnostic that never contains response bodies or credentials."""


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        self.print_usage(sys.stderr)
        self.exit(2, "FAIL: 参数无效；请查看 --help。不要通过命令行参数提供密码。\n")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def passed(name: str) -> None:
    print("PASS " + name, flush=True)


def expect_status(response: httpx.Response, code: int, name: str) -> None:
    require(response.status_code == code, f"{name}：HTTP {response.status_code}，预期 {code}")


def response_object(response: httpx.Response, name: str) -> dict:
    try:
        value = response.json()
    except ValueError:
        raise SmokeError(name + "：响应格式无效") from None
    require(isinstance(value, dict), name + "：响应格式无效")
    return value


def contains_point(points: list[tuple[float, float]], point: tuple[float, float]) -> bool:
    x, y = point
    inside = False
    for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1], strict=True):
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return inside


def verify_drawing(data: bytes) -> None:
    """Check real downloaded DXF geometry; let ezdxf detect legacy text codepages."""
    require(0 < len(data) <= 25 * 1024 * 1024, "下载的 DXF 大小异常")
    try:
        with tempfile.TemporaryDirectory(prefix="cad-qa-smoke-") as directory:
            path = Path(directory) / "drawing.dxf"
            path.write_bytes(data)
            doc = ezdxf.readfile(path)
        entities = list(doc.modelspace())
        units = int(doc.header.get("$INSUNITS", 0))
        require(units != 0, "验收图纸缺少明确的绘图单位")
        scale = conversion_factor(units, 6)  # metres
        doors = [e for e in entities if e.dxftype() == "INSERT"
                 and e.dxf.name.upper() == "DOOR_M0921"]
        require(len(doors) == 2, "门图块实例数量不是 2")
        labels = []
        for entity in entities:
            kind = entity.dxftype()
            if kind not in {"TEXT", "MTEXT"}:
                continue
            text = entity.plain_text() if kind == "MTEXT" else entity.dxf.text
            text = re.sub(r"\\U\+([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), text)
            if "会议室" in text:
                labels.append((entity.dxf.insert.x, entity.dxf.insert.y))
        require(bool(labels), "会议室中文文字未保留")
        found = False
        for entity in entities:
            if entity.dxftype() != "LWPOLYLINE" or not entity.closed:
                continue
            vertices = list(entity.get_points("xyb"))
            if len(vertices) < 3 or any(abs(v[2]) > 1e-12 for v in vertices):
                continue
            points = [(float(v[0]), float(v[1])) for v in vertices]
            if not all(math.isfinite(v) for point in points for v in point):
                continue
            edges = list(zip(points, points[1:] + points[:1], strict=True))
            area = abs(sum(ax * by - bx * ay for (ax, ay), (bx, by) in edges)) / 2
            perimeter = sum(math.hypot(bx - ax, by - ay) for (ax, ay), (bx, by) in edges)
            if (math.isclose(area * scale**2, 16, abs_tol=1e-4)
                    and math.isclose(perimeter * scale, 16, abs_tol=1e-4)
                    and any(contains_point(points, point) for point in labels)):
                found = True
                break
        require(found, "未找到会议室候选闭合轮廓 16㎡、周长 16m")
    except SmokeError:
        raise
    except Exception:
        raise SmokeError("下载的 DXF 无法完成几何解析") from None


def verify_png(data: bytes) -> None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            require(image.format == "PNG", "静态预览不是 PNG")
            require(0 < image.width * image.height <= 20_000_000, "静态预览尺寸异常")
            rgba = image.convert("RGBA")
            white = Image.new("RGBA", rgba.size, "white")
            white.alpha_composite(rgba)
            histogram = ImageOps.grayscale(white.convert("RGB")).histogram()
            require(sum(histogram[:245]) > 100, "静态 PNG 预览为空白")
    except SmokeError:
        raise
    except Exception:
        raise SmokeError("静态 PNG 预览无法解码") from None


def read_password(from_stdin: bool) -> str:
    if from_stdin:
        value = sys.stdin.readline(4098).removesuffix("\n").removesuffix("\r")
    else:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                value = getpass.getpass("管理员密码：")
        except (getpass.GetPassWarning, EOFError):
            raise SmokeError(
                "无法安全读取密码；请使用 --password-stdin 从受保护管道输入"
            ) from None
    require(0 < len(value) <= 1024, "密码输入为空或过长")
    return value


def smoke(args) -> None:
    parsed = urlsplit(args.base_url)
    require(parsed.scheme == "https" and bool(parsed.hostname), "--base-url 必须为 HTTPS 地址")
    require(not (parsed.username or parsed.password or parsed.query or parsed.fragment),
            "--base-url 不得含凭证、查询参数或片段")
    base = args.base_url.rstrip("/")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    prefix = parsed.path.rstrip("/")
    context = ssl.create_default_context(cafile=str(args.ca) if args.ca else None)
    fixtures = [(kind, PROJECT_ROOT / "local_examples" / ("architectural-demo." + kind))
                for kind in ("dxf", "dwg")]
    require(all(path.is_file() for _, path in fixtures), "缺少随项目发布的合成 DXF/DWG 验收图纸")
    password = read_password(args.password_stdin)
    csrf = None
    active_login = False
    timeout = httpx.Timeout(120, connect=10)
    with httpx.Client(
        verify=context, timeout=timeout, follow_redirects=False, trust_env=False
    ) as client:
        try:
            page = client.get(base + "/local-qa")
            require(page.status_code in {302, 303, 307, 308}, "匿名页面未跳转登录")
            target = urlsplit(urljoin(str(page.url), page.headers.get("location", "")))
            require(f"{target.scheme}://{target.netloc}" == origin
                    and target.path.rstrip("/") == prefix + "/login", "匿名页面登录跳转地址异常")
            passed("匿名页面跳转登录")
            for path in ("local-status", "local-demo", "local-font", "dxf", "render", "download"):
                expect_status(client.get(base + "/api/" + path), 401, "匿名 API")
            for path in ("upload", "local-qa", "entities/near"):
                expect_status(client.post(base + "/api/" + path, json={}), 401, "匿名图纸操作")
            passed("匿名图纸、上传及问答 API 返回 401")

            response = client.post(base + "/api/auth/login", headers={"Origin": origin},
                                   json={"username": args.username, "password": password})
            password = ""
            expect_status(response, 200, "管理员登录")
            session = response_object(response, "管理员登录")
            csrf = session.get("csrf_token")
            require(isinstance(csrf, str) and bool(csrf), "登录未返回 CSRF 令牌")
            active_login = True
            token = None
            for header in response.headers.get_list("set-cookie"):
                cookies = SimpleCookie()
                cookies.load(header)
                if COOKIE_NAME in cookies:
                    cookie = cookies[COOKIE_NAME]
                    require(bool(cookie["secure"]) and bool(cookie["httponly"]),
                            "会话 Cookie 缺少 Secure 或 HttpOnly")
                    require(cookie["samesite"].lower() in {"strict", "lax"},
                            "会话 Cookie 缺少适当 SameSite")
                    token = cookie.value
            require(bool(token), "登录未设置管理员会话 Cookie")
            expect_status(client.get(base + "/api/auth/session"), 200, "会话认证")
            expect_status(client.get(base + "/local-qa"), 200, "登录后页面")
            passed("登录、会话及 Secure/HttpOnly/SameSite Cookie")
            headers = {"Origin": origin, "X-CSRF-Token": csrf}
            drawing_id = None
            for kind, path in fixtures:
                with path.open("rb") as stream:
                    upload = client.post(base + "/api/upload", headers=headers,
                                         files={"file": (
                                             path.name, stream, "application/octet-stream"
                                         )})
                expect_status(upload, 200, kind.upper() + " 上传")
                result = response_object(upload, kind.upper() + " 上传")
                drawing_id = result.get("session_id")
                info = result.get("file_info", {})
                require(isinstance(drawing_id, str) and bool(drawing_id) and isinstance(info, dict),
                        kind.upper() + " 上传响应格式无效")
                require(
                    info.get("source_format") == kind and info.get("normalized_format") == "dxf",
                    kind.upper() + " 来源格式或归一化格式异常",
                )
                if kind == "dwg":
                    require(isinstance(info.get("conversion_notice"), str)
                            and bool(info["conversion_notice"].strip()), "DWG 缺少转换兼容性提示")
                passed(kind.upper() + " 上传及来源格式")
                params = {"session_id": drawing_id}
                drawing = client.get(base + "/api/dxf", params=params)
                expect_status(drawing, 200, kind.upper() + " 图纸读取")
                verify_drawing(drawing.content)
                passed(kind.upper() + " 中文、门图块 2 个、会议室候选轮廓 16㎡/16m")
                preview = client.get(base + "/api/render", params=params)
                expect_status(preview, 200, kind.upper() + " 静态预览")
                verify_png(preview.content)
                passed(kind.upper() + " PNG 可解码且有实际图纸内容")

            expect_status(client.post(base + "/api/auth/logout", headers=headers), 200, "退出")
            active_login = False
            expect_status(client.get(base + "/api/dxf", params={"session_id": drawing_id}), 401,
                          "退出后图纸访问")
            passed("退出后图纸请求返回 401")
            with httpx.Client(verify=context, timeout=timeout, trust_env=False) as replay:
                response = replay.get(base + "/api/dxf", params={"session_id": drawing_id},
                                      headers={"Cookie": COOKIE_NAME + "=" + token})
                expect_status(response, 401, "撤销会话重放")
            passed("旧会话 Cookie 重放返回 401")
        finally:
            if active_login and csrf:
                with suppress(httpx.HTTPError):
                    client.post(base + "/api/auth/logout",
                                headers={"Origin": origin, "X-CSRF-Token": csrf})


def main() -> int:
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="HTTPS 入口，例如 https://localhost:8443/cad")
    parser.add_argument("--ca", type=Path, help="用于验证服务证书的 PEM CA；省略时使用系统信任库")
    parser.add_argument("--username", default="admin")
    parser.add_argument(
        "--password-stdin", action="store_true",
        help="从标准输入首行读取密码，默认安全交互输入",
    )
    args = parser.parse_args()
    try:
        smoke(args)
        passed("HTTPS 离线部署验收完成（未调用 DeepSeek）")
        return 0
    except SmokeError as error:
        print("FAIL " + str(error), file=sys.stderr)
    except httpx.HTTPError:
        print("FAIL HTTPS 连接、TLS 验证或请求超时", file=sys.stderr)
    except OSError:
        print("FAIL 无法读取证书或本地验收文件", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("FAIL 验收已取消或缺少输入", file=sys.stderr)
    except Exception as error:
        print("FAIL 验收工具未完成：" + type(error).__name__, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
