"""配置层测试：三组通用参数（LLM/EMBEDDING/VISION）的回退、mode() 配置巡检与视觉开关。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_config.py -v
"""
import importlib
import os
import sys

# 清空厂商 key（覆盖 .env 里可能存在的值），必须在 import app 之前设置
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import ai, config  # noqa: E402


def _reload() -> None:
    """重读 env 重建 settings（类属性在 import 时定型，测试后务必再 reload 还原）。"""
    importlib.reload(config)


def test_mode_reports_missing_when_no_keys() -> None:
    """全部留空：mode() 报 llm/embedding missing、视觉 off（只报配置与否，不代表连通）。"""
    assert ai.mode() == {"llm": "missing", "embedding": "missing", "vision": "off"}
    assert config.settings.vision_enabled is False


def test_mode_reports_configured(monkeypatch) -> None:
    """三组 key/模型填上后 mode() 报 configured/on（直接在实例上改，不重载模块）。"""
    monkeypatch.setattr(config.settings, "LLM_API_KEY", "k")
    monkeypatch.setattr(config.settings, "EMBEDDING_API_KEY", "k")
    monkeypatch.setattr(config.settings, "VISION_API_KEY", "k")
    monkeypatch.setattr(config.settings, "VISION_MODEL", "vl-test")
    assert ai.mode() == {"llm": "configured", "embedding": "configured", "vision": "on"}


def test_embedding_and_vision_fall_back_to_llm(monkeypatch) -> None:
    """EMBEDDING/VISION 的 KEY 与 BASE_URL 留空时回退 LLM 组：同厂商只需配 LLM_API_KEY。"""
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    # 置空而非删除：os.environ 里的空串会挡住 load_dotenv 回填 .env 里的真实值
    for k in ("EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "VISION_API_KEY",
              "VISION_BASE_URL", "VISION_MODEL"):
        monkeypatch.setenv(k, "")
    _reload()
    try:
        s = config.settings
        assert s.EMBEDDING_API_KEY == "k" and s.EMBEDDING_BASE_URL == "https://llm.example/v1"
        assert s.VISION_API_KEY == "k" and s.VISION_BASE_URL == "https://llm.example/v1"
        assert s.vision_enabled is False  # VISION_MODEL 空 = 视觉关闭
    finally:
        monkeypatch.undo()
        _reload()


def test_vision_enabled_requires_model(monkeypatch) -> None:
    """有 key（含 LLM 回退）且配了 VISION_MODEL 才开视觉。"""
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("VISION_MODEL", "vl-test")
    _reload()
    try:
        assert config.settings.vision_enabled is True
    finally:
        monkeypatch.undo()
        _reload()


def test_embedding_explicit_overrides_fallback(monkeypatch) -> None:
    """EMBEDDING 组显式配置时不回退，可与 LLM 组不同厂商。"""
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("EMBEDDING_API_KEY", "emb-k")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://emb.example/v1")
    _reload()
    try:
        s = config.settings
        assert s.EMBEDDING_API_KEY == "emb-k"
        assert s.EMBEDDING_BASE_URL == "https://emb.example/v1"
    finally:
        monkeypatch.undo()
        _reload()
