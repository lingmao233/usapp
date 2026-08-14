"""高德数字签名（安全密钥）测试：sig 生成算法、_get 装配、无密钥兼容。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_amap.py -v
"""
import os
import sys
import tempfile

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_amap_"), "test.db")
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["DOUBAO_API_KEY"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import ai  # noqa: E402
from app.config import settings  # noqa: E402
from app.services import amap  # noqa: E402
from app.services import wishes as wishes_svc  # noqa: E402


class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"status": "1", "geocodes": [{"adcode": "110000", "location": "116.4,39.9"}]}


def _capture(monkeypatch) -> dict:
    captured: dict = {}

    def _fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _Resp()

    monkeypatch.setattr(amap.httpx, "get", _fake_get)
    return captured


def test_get_attaches_sig_when_secret_set(monkeypatch) -> None:
    """配了安全密钥：请求带 sig；算法 = 参数名升序 key=value& 连接 + 私钥，MD5 小写。"""
    monkeypatch.setattr(settings, "AMAP_KEY", "testkey")
    monkeypatch.setattr(settings, "AMAP_SECRET", "testsecret")
    captured = _capture(monkeypatch)

    result = amap.geocode_city("北京")

    assert result == {"adcode": "110000", "location": "116.4,39.9"}
    params = captured["params"]
    # 已知答案：md5("address=北京&key=testkey" + "testsecret")——锁定参数排序与私钥拼接
    assert params["sig"] == "9c0093d5672aba016b5e5ed3da43b360"


def test_get_no_sig_without_secret(monkeypatch) -> None:
    """未配安全密钥：不带 sig（未绑密钥的老 key 行为不变）。"""
    monkeypatch.setattr(settings, "AMAP_KEY", "testkey")
    monkeypatch.setattr(settings, "AMAP_SECRET", "")
    captured = _capture(monkeypatch)

    amap.geocode_city("北京")

    assert "sig" not in captured["params"]
    assert captured["params"]["key"] == "testkey"


def test_get_skips_call_without_key(monkeypatch) -> None:
    """无 key：不发请求，直接 None（回退纯 LLM 方案）。"""
    monkeypatch.setattr(settings, "AMAP_KEY", "")
    monkeypatch.setattr(settings, "AMAP_SECRET", "testsecret")

    def _boom(*a, **kw):
        raise AssertionError("无 key 不应发起请求")

    monkeypatch.setattr(amap.httpx, "get", _boom)
    assert amap.geocode_city("北京") is None


# ---------- 方案上下文：愿望分析决定是否查高德（BUG-007） ----------

def test_plan_context_skips_amap_for_person_wish(monkeypatch) -> None:
    """约人类愿望（need_real_data=False）：不查高德，scene 透传给方案 prompt。"""
    monkeypatch.setattr(settings, "AMAP_KEY", "testkey")
    monkeypatch.setattr(settings, "AMAP_SECRET", "")
    monkeypatch.setattr(ai, "extract_plan_query", lambda c: {
        "city": "", "keywords": "", "need_real_data": False, "scene": "约朋友欧培昇一起玩",
    })

    monkeypatch.setattr(amap, "gather", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不应查高德")))
    q, real = wishes_svc._plan_context("想找欧培昇玩")

    assert real is None
    assert "欧培昇" in q["scene"]


def test_plan_context_queries_amap_for_place_wish(monkeypatch) -> None:
    """地点类愿望（need_real_data=True）：正常查高德并透传分析结果。"""
    monkeypatch.setattr(settings, "AMAP_KEY", "testkey")
    monkeypatch.setattr(ai, "extract_plan_query", lambda c: {
        "city": "青岛", "keywords": "海边", "need_real_data": True, "scene": "周边游",
    })
    called: dict = {}

    def _fake_gather(city, keywords):
        called["args"] = (city, keywords)
        return {"spots": [{"name": "第一海水浴场"}]}

    monkeypatch.setattr(amap, "gather", _fake_gather)
    q, real = wishes_svc._plan_context("想去海边")

    assert called["args"] == ("青岛", "海边")
    assert real == {"spots": [{"name": "第一海水浴场"}]}
    assert q["scene"] == "周边游"


def test_plan_context_no_key_still_analyzes(monkeypatch) -> None:
    """未配 AMAP_KEY：照常分析（scene 可用），但不发高德请求。"""
    monkeypatch.setattr(settings, "AMAP_KEY", "")
    monkeypatch.setattr(ai, "extract_plan_query", lambda c: {
        "city": "青岛", "keywords": "海边", "need_real_data": True, "scene": "周边游",
    })
    monkeypatch.setattr(
        amap, "gather", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("无 key 不应查"))
    )
    q, real = wishes_svc._plan_context("想去海边")

    assert real is None
    assert q["scene"] == "周边游"


def test_mock_extract_returns_analysis_keys() -> None:
    """mock 愿望分析桩确定性产出新字段（activity + need_real_data 恒 True 维持旧行为）。"""
    q = ai.extract_plan_query("想找欧培昇玩")
    assert q["kind"] == "activity"
    assert q["need_real_data"] is True
    assert q["keywords"] == "想找欧培昇玩"
    assert q["city"] == "" and q["scene"] == "" and q["mood"] == "neutral"


# ---------- 方案 prompt：kind 决定实现路径（纯函数断言） ----------

def test_build_plan_prompt_kind_strategy() -> None:
    """kind 枚举映射到写死的实现路径与类型人格；非法 kind 回退 activity。"""
    # 情绪倾诉·非消极：损友玩梗人格进 prompt（含示例梗），护栏在
    p = ai.build_plan_prompt("想暴富", ["阿澈"], analysis={"kind": "venting", "scene": "玩梗", "mood": "playful"})
    assert "口吻写：损友" in p and "玩梗方案" in p and "梦里什么都有" in p
    assert "不拿别人开涮" in p
    # 情绪倾诉·消极：换树洞人格，不走损友口吻
    p_neg = ai.build_plan_prompt("想不工作，好累", ["阿澈"], analysis={"kind": "venting", "scene": "吐槽工作", "mood": "negative"})
    assert "口吻写：温柔树洞" in p_neg and "积极小行动" in p_neg
    # 约人：围绕怎么约 TA，地点只是轻建议
    p2 = ai.build_plan_prompt("想找欧培昇玩", ["阿澈"], analysis={"kind": "meet", "scene": "约朋友"})
    assert "约人聚会" in p2 and "约上 TA" in p2
    # 学习技能：鼓励型教练人格 + 入门计划路径
    p3 = ai.build_plan_prompt("想学游泳", ["阿澈"], analysis={"kind": "learning", "scene": "入门游泳"})
    assert "口吻写：鼓励型教练" in p3 and "入门计划" in p3
    # 非法 kind 回退 activity；无分析时同样兜底且无真实数据口径
    p4 = ai.build_plan_prompt("想干啥", ["阿澈"], analysis={"kind": "bogus"})
    assert "活动/购物/小事" in p4
    p5 = ai.build_plan_prompt("想去海边", ["阿澈"])
    assert "活动/购物/小事" in p5 and "没有可调用的真实数据" in p5


def test_extract_plan_query_real_path_parsing(monkeypatch) -> None:
    """真实路径解析：kind/mood 小写化与枚举校验、字符串布尔防御、need 与 keywords 联动。"""
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "x")  # llm_mock → False
    monkeypatch.setattr(
        ai.deepseek, "chat_json",
        lambda prompt: {"kind": "Venting", "mood": "NEGATIVE", "need_real_data": "false",
                        "keywords": "", "city": "", "scene": "丧"},
    )
    q = ai.extract_plan_query("想不工作，好累")
    assert q["kind"] == "venting" and q["mood"] == "negative"
    assert q["need_real_data"] is False  # 字符串布尔防御 + keywords 空联动


def test_wish_match_prompt_kind_guidance() -> None:
    """共同愿望建议 prompt 携带分类型出招指引（BUG-008：suggestion 曾无人格写成理财课）。"""
    from app.ai.prompts import WISH_MATCH_PROMPT
    assert "刮刮乐" in WISH_MATCH_PROMPT and "消极情绪" in WISH_MATCH_PROMPT
    assert "人名" in WISH_MATCH_PROMPT
