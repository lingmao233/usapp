"""配置管理：从 .env 读取，缺失 API key 时自动进入 mock 模式。"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 依次尝试 server/.env 与项目根目录 .env
_SERVER_DIR = Path(__file__).resolve().parent.parent
_ROOT_DIR = _SERVER_DIR.parent
for _p in (_SERVER_DIR / ".env", _ROOT_DIR / ".env"):
    if _p.exists():
        load_dotenv(_p, override=False)


class Settings:
    # 文本 LLM（OpenAI 兼容 chat/completions）：分类/摘要/周报/方案/画像
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "")

    # Embedding（OpenAI 兼容 /embeddings，纯文本向量）
    # KEY/BASE_URL 留空时回退 LLM 组：同厂商只需配 LLM_API_KEY
    EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY", "") or LLM_API_KEY
    EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL", "") or LLM_BASE_URL
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "")
    # 向量维度：调用时显式传，保证全库同维度（换模型/改维度后必须跑 scripts/reembed.py 回填）
    EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "1024"))

    # 视觉模型（图片 caption / 账单识别 / 食物识别）：VISION_MODEL 空 = 视觉关闭，优雅跳过
    # KEY/BASE_URL 同样回退 LLM 组
    VISION_API_KEY: str = os.getenv("VISION_API_KEY", "") or LLM_API_KEY
    VISION_BASE_URL: str = os.getenv("VISION_BASE_URL", "") or LLM_BASE_URL
    VISION_MODEL: str = os.getenv("VISION_MODEL", "")
    # caption/识别调用的思考强度（minimal/low/medium/high），空 = 不传参；深度思考类模型建议 minimal
    VISION_REASONING: str = os.getenv("VISION_REASONING", "")

    # 高德 Web 服务（方案真实数据：POI/天气/通勤）：空 = 回退纯 LLM 经验方案
    AMAP_KEY: str = os.getenv("AMAP_KEY", "")
    # 高德安全密钥（可选）：非空时每个请求自动带 sig 数字签名；空 = 不签名（兼容未绑密钥的老 key）
    AMAP_SECRET: str = os.getenv("AMAP_SECRET", "")

    # Redis（可选，连不上自动降级进程内字典）
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # SQLite
    DB_PATH: str = os.getenv("DB_PATH", str(_SERVER_DIR / "data" / "app.db"))

    # Web Push（第 5 期）：VAPID claims 的联系方式，规范要求 mailto: 或 https:
    VAPID_SUB: str = os.getenv("VAPID_SUB", "mailto:us-app@localhost")

    @property
    def llm_mock(self) -> bool:
        return not self.LLM_API_KEY

    @property
    def embed_mock(self) -> bool:
        return not self.EMBEDDING_API_KEY

    @property
    def vision_enabled(self) -> bool:
        """视觉开关：配了 key（含 LLM 回退）且配了 VISION_MODEL 才启用。"""
        return bool(self.VISION_API_KEY and self.VISION_MODEL)

    @property
    def upload_dir(self) -> Path:
        """图片上传目录：跟随 DB_PATH 所在目录（测试库指向临时目录时自动隔离）。"""
        return Path(self.DB_PATH).parent / "uploads"


settings = Settings()
