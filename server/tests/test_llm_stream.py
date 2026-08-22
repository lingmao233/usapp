"""llm 流式 SSE 增量累积器单元测试（纯逻辑离线测，不触网）。

_OpenAI 线格式分片到达：content 逐段、tool_calls 的 id/name/arguments 按 index 分片拼接；
finalize 还原成完整 assistant 消息（tool_calls type 归一 function，BUG-017 口径）。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_llm_stream.py -v
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai.llm import _StreamAccum  # noqa: E402


def _chunk(delta: dict, finish: str | None = None) -> str:
    choice: dict = {"delta": delta}
    if finish:
        choice["finish_reason"] = finish
    return json.dumps({"choices": [choice]}, ensure_ascii=False)


def test_content_accumulates_in_order() -> None:
    acc = _StreamAccum()
    out = ""
    for piece in ("我在", "呢。", "你说的我都听见了"):
        out += acc.feed(_chunk({"content": piece}))
    assert out == "我在呢。你说的我都听见了"       # feed 返回本段可外发内容
    assert acc.saw_tool_call is False
    msg = acc.finalize()
    assert msg["content"] == "我在呢。你说的我都听见了" and msg["tool_calls"] == []


def test_tool_call_fragments_merge_by_index() -> None:
    """$web_search 回声协议：name/arguments 分片到达，按 index 拼接、type 归一 function。"""
    acc = _StreamAccum()
    acc.feed(_chunk({"tool_calls": [{"index": 0, "id": "call_1",
                                     "function": {"name": "$web_", "arguments": '{"qu'}}]}))
    acc.feed(_chunk({"tool_calls": [{"index": 0, "function": {"name": "search",
                                     "arguments": 'ery": "红茶"}'}}]}))
    acc.feed(_chunk({}, finish="tool_calls"))
    assert acc.saw_tool_call is True
    msg = acc.finalize()
    tc = msg["tool_calls"][0]
    assert tc["id"] == "call_1" and tc["type"] == "function"
    assert tc["function"]["name"] == "$web_search"
    assert json.loads(tc["function"]["arguments"]) == {"query": "红茶"}


def test_content_after_tool_call_seen_is_buffered_from_stream() -> None:
    """工具调用出现后，本轮后续 content 仍进全文（finalize 完整），但 feed 的返回值
    供外发判断（_post_streaming 据此不把过渡叙述推给用户）。"""
    acc = _StreamAccum()
    first = acc.feed(_chunk({"content": "让我查查"}))
    assert first == "让我查查"
    acc.feed(_chunk({"tool_calls": [{"index": 0, "function": {"name": "$web_search"}}]}))
    after = acc.feed(_chunk({"content": "（内部叙述）"}))
    assert after == "（内部叙述）"  # 累积器如实返回；外发与否由 _post_streaming 按 saw_tool_call 决定
    assert acc.finalize()["content"] == "让我查查（内部叙述）"


def test_finish_reason_tracked() -> None:
    acc = _StreamAccum()
    acc.feed(_chunk({"content": "hi"}))
    acc.feed(_chunk({}, finish="stop"))
    assert acc.finish_reason == "stop"
