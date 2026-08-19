"""pytest 会话基建：独立测试库 + 清空厂商 key + 把 AI 门面换成 tests/fakes.py 确定性桩。

- fakes 在 conftest import 期就装上（模块级 install）：测试模块 import 阶段的
  init_db 种子灌库（_seed_food_nutrition 走 ai.embed_text）也落在桩上，绝不触网
- autouse fixture 每个用例再幂等重装一次：用例内 monkeypatch 的临时覆盖在 teardown
  自动还原回桩，桩状态不跨用例泄漏
- 需要断言真实解析路径的用例：monkeypatch.setattr(ai, "...", fakes.REAL_IMPLS["..."])
  装回真身，再 monkeypatch provider 层（ai.llm.chat_json 等）喂受控响应
"""
import os
import sys
import tempfile

# 必须在 import app 之前：独立测试库 + 清空厂商 key（load_dotenv override=False，
# 已存在的环境变量优先，空串能挡住 .env 回填）
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_"), "test.db")
for _k in ("LLM_API_KEY", "EMBEDDING_API_KEY", "VISION_API_KEY", "VISION_MODEL"):
    os.environ[_k] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from app import ai as _ai  # noqa: E402
import fakes as _fakes  # noqa: E402

_fakes.install(_ai)


@pytest.fixture(autouse=True)
def _ai_fakes() -> None:
    """每个用例前幂等重装确定性桩（防裸赋值泄漏；monkeypatch 覆盖会自动还原回桩）。"""
    _fakes.install(_ai)
