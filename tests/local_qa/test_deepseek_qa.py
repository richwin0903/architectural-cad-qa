"""CAD evidence correctness and bounded API contract tests (no external calls)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import ezdxf
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from web.backend.deepseek_qa import (
    DeepSeekQA,
    DrawingTools,
    QARequest,
    create_local_qa_router,
)

from cad_dxf_agent.core.dxf_reader import load_dxf
from cad_dxf_agent.models.cad_schema import DrawingUnit


@pytest.fixture
def drawing(tmp_path):
    doc = ezdxf.new("R2018")
    doc.units = 4
    block = doc.blocks.new("DOOR_M0921")
    block.add_line((0, 0), (900, 0))
    for i in range(61):
        ref = doc.modelspace().add_blockref("DOOR_M0921", (i * 1000, -500))
        ref.add_attrib("TYPE", "M0921")
        ref.add_attrib("LOCATION", "会议室" if i == 0 else "走廊")
    # A paper-space instance must not inflate the default model-space count.
    doc.layouts.new("二层出图").add_blockref("DOOR_M0921", (0, 0))
    rectangle = doc.modelspace().add_lwpolyline(
        [(0, 0), (6000, 0), (6000, 4000), (0, 4000)],
        close=True,
    )
    label = doc.modelspace().add_text("会议室", dxfattribs={"insert": (1000, 1000)})
    curved = doc.modelspace().add_lwpolyline(
        [(0, 0, 1), (10, 0, 0), (10, 10, 0)],
        format="xyb",
        close=True,
    )
    crossing = doc.modelspace().add_lwpolyline(
        [(0, 0), (10, 10), (0, 10), (10, 0)],
        close=True,
    )
    open_line = doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 10)])
    path = tmp_path / "qa.dxf"
    doc.saveas(path)
    return SimpleNamespace(
        context=load_dxf(path),
        path=path,
        rectangle=rectangle.dxf.handle,
        label=label.dxf.handle,
        curved=curved.dxf.handle,
        crossing=crossing.dxf.handle,
        open_line=open_line.dxf.handle,
    )


def test_count_is_full_and_layout_scoped_even_when_evidence_is_paginated(drawing):
    tools = DrawingTools(drawing.context)
    query = tools.execute("query_entities", {"block_name": "DOOR_M0921", "limit": 2})
    assert query["total_matches"] == 61
    assert len(query["entities"]) == 2
    assert query["has_more"]
    count = tools.execute("count_entities", {"block_name": "DOOR_M0921"})
    assert count["total"] == 61
    all_spaces = tools.execute("count_entities", {"block_name": "DOOR_M0921", "space": "*"})
    assert all_spaces["total"] == 62


def test_searches_non_first_block_attribute_and_preserves_handle(drawing):
    tools = DrawingTools(drawing.context)
    result = tools.execute(
        "query_entities",
        {
            "entity_type": "INSERT",
            "text_contains": "会议室",
        },
    )
    assert result["total_matches"] == 1
    entity = result["entities"][0]
    assert entity["block_attributes"]["LOCATION"] == "会议室"
    assert entity["handle"] in tools.observed


def test_measure_validates_raw_geometry_and_units_without_editing(drawing):
    original = drawing.path.read_bytes()
    tools = DrawingTools(drawing.context)
    area = tools.measure(drawing.rectangle)
    assert area["area_m2"] == pytest.approx(24)
    assert area["area_drawing_units_squared"] == pytest.approx(24_000_000)
    assert area["perimeter_m"] == pytest.approx(20)
    assert area["perimeter_drawing_units"] == pytest.approx(20_000)
    assert area["is_room"] is False
    assert area["contained_text_candidates"][0]["handle"] == drawing.label
    assert "圆弧" in tools.measure(drawing.curved)["error"]
    assert "自交" in tools.measure(drawing.crossing)["error"]
    assert "闭合" in tools.measure(drawing.open_line)["error"]
    assert drawing.path.read_bytes() == original


def test_unknown_units_never_become_square_meters(drawing):
    drawing.context.drawing_unit = DrawingUnit.UNITLESS
    area = DrawingTools(drawing.context).measure(drawing.rectangle)
    assert area["area_m2"] is None
    assert area["perimeter_m"] is None
    assert area["perimeter_drawing_units"] == pytest.approx(20_000)
    assert area["area_drawing_units_squared"] == pytest.approx(24_000_000)
    assert "禁止当作平方米" in area["warning"]


def test_unrecognized_tools_and_extra_parameters_cannot_write_or_read_paths(drawing):
    tools = DrawingTools(drawing.context)
    assert "error" in tools.execute("delete_entity", {"handle": drawing.rectangle})
    assert "error" in tools.execute(
        "measure_contours",
        {
            "handles": [drawing.rectangle],
            "file_path": "/etc/passwd",
        },
    )
    assert "error" in tools.execute("query_entities", {"limit": 10000})


def _request():
    return QARequest(session_id="example", question="图中 M0921 图块有几个？")


def test_real_api_payload_tool_loop_and_verified_evidence(monkeypatch, drawing):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-placeholder")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    payloads = []

    def handler(request):
        assert request.url == "https://api.deepseek.com/chat/completions"
        payload = json.loads(request.content)
        payloads.append(payload)
        assert payload["thinking"] == {"type": "disabled"}
        if len(payloads) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_count",
                                        "type": "function",
                                        "function": {
                                            "name": "count_entities",
                                            "arguments": json.dumps({"block_name": "DOOR_M0921"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        tool_result = json.loads(
            next(m["content"] for m in reversed(payload["messages"]) if m["role"] == "tool")
        )
        assert tool_result["total"] == 61
        handle = tool_result["sample_entities"][0]["handle"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "answer": f"有 61 个该图块实例 [图元 {handle}]。",
                                    "evidence_handles": [handle, "NONEXISTENT"],
                                },
                                ensure_ascii=False,
                            ),
                        }
                    }
                ]
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await DeepSeekQA().answer(_request(), drawing.context, client)

    result = asyncio.run(run())
    assert len(payloads) == 2
    assert "61" in result["answer"]
    assert result["highlight_handles"] == [result["evidence"][0]["handle"]]
    assert "NONEXISTENT" not in result["highlight_handles"]
    assert any("未核验" in warning for warning in result["warnings"])
    assert result["tools_used"][-1]["name"] == "count_entities"


@pytest.mark.parametrize(
    "status,message", [(401, "密钥验证失败"), (402, "余额不足"), (429, "限流"), (500, "HTTP 500")]
)
def test_upstream_error_is_actionable_but_does_not_echo_body(
    monkeypatch,
    drawing,
    status,
    message,
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-placeholder")

    async def run():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                status,
                text="secret-upstream-body local-test-placeholder",
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            return await DeepSeekQA().answer(_request(), drawing.context, client)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(run())
    assert message in exc.value.detail
    assert "secret-upstream-body" not in exc.value.detail
    assert "local-test-placeholder" not in exc.value.detail


def test_missing_key_is_explicit_no_mock_fallback(monkeypatch, drawing):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(DeepSeekQA().answer(_request(), drawing.context))
    assert exc.value.status_code == 503


def test_tool_loop_has_hard_limit(monkeypatch, drawing):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-placeholder")
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": f"call_{len(calls)}",
                                    "type": "function",
                                    "function": {
                                        "name": "count_entities",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await DeepSeekQA().answer(_request(), drawing.context, client)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(run())
    assert "上限" in exc.value.detail
    assert len(calls) == 6
    assert calls[-1]["tool_choice"] == "none"


def test_route_enforces_session_owner_and_validates_input(drawing, monkeypatch):
    received = []

    class Manager:
        def get(self, session_id, user_id):
            if session_id != "owned" or user_id != "alice":
                raise PermissionError
            return SimpleNamespace(context=drawing.context)

    async def user():
        return {"uid": "alice"}

    async def answer(self, request, context):
        received.append(context)
        return {"answer": "测试 API 合同", "model": "test"}

    monkeypatch.setattr(DeepSeekQA, "answer", answer)
    app = FastAPI()
    app.include_router(create_local_qa_router(Manager(), user))
    client = TestClient(app)
    assert (
        client.post(
            "/api/local-qa",
            json={
                "session_id": "someone-else",
                "question": "统计",
            },
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/local-qa",
            json={
                "session_id": "owned",
                "question": "统计",
                "history": [{"role": "system", "content": "x"}],
            },
        ).status_code
        == 422
    )
    result = client.post("/api/local-qa", json={"session_id": "owned", "question": "统计"})
    assert result.status_code == 200
    assert received == [drawing.context]


def test_prose_before_answer_json_is_not_displayed_twice_and_citations_stay_checked(drawing):
    tools = DrawingTools(drawing.context)
    tools.measure(drawing.rectangle)
    payload = json.dumps(
        {
            "answer": f"面积 24㎡ [图元 {drawing.rectangle}]。",
            "evidence_handles": [drawing.rectangle, "NONEXISTENT"],
        },
        ensure_ascii=False,
    )
    result = DeepSeekQA()._final("重复的解释正文。\n\n" + payload, tools, [])
    assert result["answer"] == f"面积 24㎡ [图元 {drawing.rectangle}]。"
    assert result["highlight_handles"] == [drawing.rectangle]
    assert any("未核验" in warning for warning in result["warnings"])
