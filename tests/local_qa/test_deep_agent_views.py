"""Global coverage, zoom, containment and readonly agent boundaries; no paid calls."""

import asyncio
import base64
import io
import json
from types import SimpleNamespace

import ezdxf
import httpx
import pytest
from fastapi import HTTPException
from PIL import Image, ImageChops
from web.backend.cad_deep_agent import ALLOWED_TOOLS, MAX_TOTAL_TOOLS, CadBoundary
from web.backend.cad_visual import DrawingViews, ViewArgs
from web.backend.deepseek_qa import DeepSeekQA, DrawingTools, QARequest

from cad_dxf_agent.core.dxf_reader import load_dxf
from cad_dxf_agent.core.renderer import render_dxf_to_png


@pytest.fixture
def multiview(tmp_path):
    doc = ezdxf.new("R2018")
    model = doc.modelspace()
    for x, y, w, h in [(0, 0, 40, 40), (0, 60, 40, 40), (100, 50, 80, 30)]:
        model.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True)
    model.add_text("平面图", dxfattribs={"insert": (0, 45), "height": 3})
    model.add_text("立面图", dxfattribs={"insert": (100, 85), "height": 3})
    doc.layouts.new("详图").add_circle((0, 0), 10)
    path = tmp_path / "synthetic-multiview.dxf"
    doc.saveas(path)
    return load_dxf(path)


def png(view):
    return Image.open(io.BytesIO(base64.b64decode(view["image"]))).convert("RGB")


def has_ink(image):
    return ImageChops.difference(image, Image.new("RGB", image.size, "white")).getbbox() is not None


def test_global_render_covers_left_right_and_other_layout_zoom_has_world_bounds(multiview):
    views = DrawingViews(multiview)
    layouts = views.prepare()
    assert {layout["space"] for layout in layouts if layout["full_preview_available"]} == {
        "Model",
        "详图",
    }
    full = png(views.views["view-1"])
    assert has_ink(full.crop((0, 0, full.width // 2, full.height)))
    assert has_ink(full.crop((full.width // 2, 0, full.width, full.height)))
    cropped = views.render(ViewArgs(x0=0.5))
    assert cropped["bounds"]["minX"] == pytest.approx(
        (views.views["view-1"]["bounds"]["minX"] + views.views["view-1"]["bounds"]["maxX"]) / 2
    )
    assert has_ink(png(views.views[cropped["id"]]))
    assert cropped["id"] == views.render(ViewArgs(x0=0.5))["id"]
    assert views.render(ViewArgs(space="../../etc/passwd")).get("error")


def test_dangling_insert_does_not_destroy_preview_or_modify_source(tmp_path):
    doc = ezdxf.new()
    doc.modelspace().add_circle((0, 0), 3)
    doc.modelspace().add_blockref("MISSING", (100, 0))
    path = tmp_path / "dangling.dxf"
    doc.saveas(path)
    original = path.read_bytes()
    result = render_dxf_to_png(path, tmp_path / "preview.png")
    assert result.success
    assert any("1 个缺失定义" in w for w in result.quality_warnings)
    assert path.read_bytes() == original


def response(message):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", **message}}]})


def test_framework_sends_full_images_first_then_zoom_and_filters_forged_views(
    monkeypatch, multiview
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-placeholder")
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert {t["function"]["name"] for t in payload["tools"]} == ALLOWED_TOOLS
        images = []
        for message in payload["messages"]:
            if isinstance(message.get("content"), list):
                for block in message["content"]:
                    if block.get("type") == "image_url":
                        assert message["role"] == "user"
                        images.append(block)
        assert len(images) == (2 if len(calls) == 1 else 3)
        assert all(b["image_url"]["url"].startswith("data:image/png;base64,") for b in images)
        if len(calls) == 1:
            return response(
                {
                    "tool_calls": [
                        {
                            "id": "zoom",
                            "type": "function",
                            "function": {"name": "render_drawing_view", "arguments": '{"x0":0.5}'},
                        }
                    ]
                }
            )
        tool = json.loads(next(m["content"] for m in payload["messages"] if m["role"] == "tool"))
        assert tool["space"] == "Model" and tool["full_sheet"] is False
        return response(
            {
                "content": json.dumps(
                    {
                        "answer": "左侧平面，右侧立面 [视图 view-3] [视图 view-999]",
                        "view_ids": ["view-3", "view-999"],
                    }
                )
            }
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await DeepSeekQA().answer(
                QARequest(session_id="test", question="有什么图？"), multiview, client
            )

    result = asyncio.run(run())
    assert result["engine"] == "deepagents"
    assert [e["handle"] for e in result["evidence"]] == ["view-3"]
    assert result["highlight_handles"] == []
    assert "[视图未核验]" in result["answer"]
    fresh = DrawingViews(multiview)
    assert not fresh.views


def test_hidden_tools_rejected_even_if_model_invents_calls_and_total_budget_applies():
    boundary = CadBoundary(SimpleNamespace(views={}))

    async def forbidden(_):
        raise AssertionError("Unauthorized tool reached execution")

    async def run():
        for name in ["execute", "task", "read_file", "write_file", "delete"]:
            message = await boundary.awrap_tool_call(
                SimpleNamespace(tool_call={"name": name, "id": name}), forbidden
            )
            assert message.status == "error"
        boundary.tool_calls = MAX_TOTAL_TOOLS
        with pytest.raises(HTTPException) as exc:
            await boundary.awrap_tool_call(
                SimpleNamespace(tool_call={"name": "write_todos", "id": "x"}), forbidden
            )
        assert "上限" in exc.value.detail

    asyncio.run(run())


def test_visual_citations_are_not_buried_under_unrequested_entity_samples(multiview):
    views = DrawingViews(multiview)
    views.prepare()
    drawing = DrawingTools(multiview)
    drawing.execute("query_entities", {})
    result = DeepSeekQA()._final(
        json.dumps({"answer": "右侧区域 [视图 view-1 右半]", "view_ids": ["view-1"]}),
        drawing,
        [],
        views,
    )
    assert [e["handle"] for e in result["evidence"]] == ["view-1"]
    assert result["highlight_handles"] == []
