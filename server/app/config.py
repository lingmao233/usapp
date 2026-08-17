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
    # DeepSeek（OpenAI 兼容）
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "")

    # 豆包 Embedding（火山方舟 OpenAI 兼容）
    DOUBAO_API_KEY: str = os.getenv("DOUBAO_API_KEY", "")
    DOUBAO_BASE_URL: str = os.getenv("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    DOUBAO_EMBEDDING_MODEL: str = os.getenv("DOUBAO_EMBEDDING_MODEL", "doubao-embedding-vision")
    # 多模态向量维度：文字与图片调用显式传同一值，保证同空间同维度。
    # 默认 1024：doubao-embedding-vision 实测支持的最小档（512 会被 400 拒绝）
    DOUBAO_EMBED_DIM: int = int(os.getenv("DOUBAO_EMBED_DIM", "1024"))
    # 视觉模型（图片 caption）：默认空 = 关闭，未配置或调用失败都优雅跳过
    DOUBAO_VISION_MODEL: str = os.getenv("DOUBAO_VISION_MODEL", "")
    # caption 调用的思考强度（minimal/low/medium/high），空 = 不传参；seed 类模型建议 minimal
    DOUBAO_VISION_REASONING: str = os.getenv("DOUBAO_VISION_REASONING", "")

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
        return not self.DEEPSEEK_API_KEY

    @property
    def embed_mock(self) -> bool:
        return not self.DOUBAO_API_KEY

    @property
    def upload_dir(self) -> Path:
        """图片上传目录：跟随 DB_PATH 所在目录（测试库指向临时目录时自动隔离）。"""
        return Path(self.DB_PATH).parent / "uploads"


settings = Settings()
