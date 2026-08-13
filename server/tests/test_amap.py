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

from app.config import settings  # noqa: E402
from app.services import amap  # noqa: E402


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
