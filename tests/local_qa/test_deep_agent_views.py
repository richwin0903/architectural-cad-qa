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


def test_previews_reuse_only_same_context_and_source_without_leaking_evidence(
    monkeypatch, multiview
):
    import web.backend.cad_visual as visual

    first = DrawingViews(multiview)
    first.prepare()
    first.render(ViewArgs(x0=0.5))
    cache = first.cache
    assert cache.bytes > 0
    assert "_qa_render_cache" not in multiview.model_dump()
    # An independent upload/context must never acquire another session's images.
    other = DrawingViews(load_dxf(multiview.file_path))
    assert other.cache is not cache and not other.cache.entries

    def forbidden_read(_):
        raise AssertionError("A cached preview must not reopen the source DXF")

    monkeypatch.setattr(visual, "prepare_render_document", forbidden_read)
    second = DrawingViews(multiview)
    assert second.views == {}  # Old view IDs are not automatically accepted as evidence.
    second.prepare()
    assert second.doc is None and second.cache_hits == 2
    second.render(ViewArgs(x0=0.5))
    assert second.cache_hits == 3 and second.rendered == 0
    assert second.views["view-3"]["image"] == first.views["view-3"]["image"]
    assert second.views["view-3"] is not first.views["view-3"]
    changed = ezdxf.readfile(multiview.file_path)
    changed.modelspace().add_line((0, 0), (999, 999))
    changed.saveas(multiview.file_path)
    assert DrawingViews(multiview).cache is not cache


def test_region_rendering_never_copies_or_crops_shared_vector_records(monkeypatch, multiview):
    from ezdxf.addons.drawing.recorder import Player

    def forbidden(*_):
        raise AssertionError("Full-sheet deep-copy/destructive crop reintroduced")

    monkeypatch.setattr(Player, "copy", forbidden)
    monkeypatch.setattr(Player, "crop_rect", forbidden)
    views = DrawingViews(multiview)
    views.prepare()
    original = tuple(views.players["Model"].records)
    bounds = views.record_bounds["Model"].copy()
    result = views.render(ViewArgs(x0=0.5))
    assert "error" not in result
    assert tuple(views.players["Model"].records) == original
    assert (views.record_bounds["Model"] == bounds).all()
    assert has_ink(png(views.views[result["id"]]))


def test_cache_evicts_by_byte_budget_and_entry_count(monkeypatch):
    import web.backend.cad_visual as visual

    monkeypatch.setattr(visual, "MAX_CACHE_BYTES", 12)
    monkeypatch.setattr(visual, "MAX_CACHED_VIEWS", 2)
    cache = visual.RenderCache(("test",))
    for key in range(5):
        cache.put(key, {"image": "12345"})
    assert list(cache.entries) == [3, 4]
    assert cache.bytes == 10
    cache.put(5, {"image": "12345678"})
    assert list(cache.entries) == [5] and cache.bytes == 8
    cache.put(6, {"image": "x" * 13})
    assert list(cache.entries) == [5] and cache.bytes == 8


def test_dense_sheet_has_full_coverage_before_first_model_call(multiview):
    doc = ezdxf.readfile(multiview.file_path)
    for index in range(1000):
        doc.modelspace().add_line((index / 10, 0), (index / 10, 10))
    doc.saveas(multiview.file_path)
    context = load_dxf(multiview.file_path)
    views = DrawingViews(context)
    views.prepare_overview()
    assert len(views.views) == 6  # Model + another layout + four overlapping regions.
    followup = DrawingViews(context)
    followup.prepare()
    followup.reuse_prepared_regions()
    assert followup.cache_hits == 6 and followup.rendered == 0
    assert followup.doc is None
