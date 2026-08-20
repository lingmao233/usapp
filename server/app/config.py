"""配置管理：从 .env 读取。LLM/EMBEDDING 未配置时 AI 调用抛 AINotConfiguredError（见 app.ai），
视觉未配置（VISION_MODEL 空）优雅跳过；不再有 mock 分流。"""
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
    # 思考强度总开关（off/minimal/low/medium/high），空 = 不传参；深度思考类模型建议 minimal。
    # "off" 映射为 enable_thinking=false（阿里系关思考写法），其余档走 reasoning_effort
    VISION_REASONING: str = os.getenv("VISION_REASONING", "")
    # 分场景思考强度（空 = 回退 VISION_REASONING；再空 = 用各自的默认值）：
    # 碎片 caption 要精准默认 high；热量识别只认菜名+估分量（热量查表算）默认 low；
    # 记账只要准确 JSON，默认 minimal 抢速度
    VISION_REASONING_CAPTION: str = os.getenv("VISION_REASONING_CAPTION", "")
    VISION_REASONING_RECEIPT: str = os.getenv("VISION_REASONING_RECEIPT", "")
    VISION_REASONING_FOOD: str = os.getenv("VISION_REASONING_FOOD", "")

    # 高德 Web 服务（方案真实数据：POI/天气/通勤）：空 = 回退纯 LLM 经验方案
    AMAP_KEY: str = os.getenv("AMAP_KEY", "")
    # 高德安全密钥（可选）：非空时每个请求自动带 sig 数字签名；空 = 不签名（兼容未绑密钥的老 key）
    AMAP_SECRET: str = os.getenv("AMAP_SECRET", "")

    # LLM 联网搜索（营养共建核验/查表未命中兜底）：on=请求体带厂商联网开关；
    # off（默认）= 不联网，web_search_food 直接返回 None 走降级
    LLM_WEB_SEARCH: str = os.getenv("LLM_WEB_SEARCH", "off")

    # 树洞专属模型（树洞全链路：路由/改写/工具/回复/压缩/图片 caption）：
    # TREEHOLE_API_KEY 非空即启用独立厂商；BASE_URL 默认 Kimi，MODEL 默认 kimi-k2.6；
    # 整组留空 = 回退 LLM_* 组
    TREEHOLE_API_KEY: str = os.getenv("TREEHOLE_API_KEY", "")
    TREEHOLE_BASE_URL: str = os.getenv("TREEHOLE_BASE_URL", "")
    TREEHOLE_MODEL: str = os.getenv("TREEHOLE_MODEL", "")
    # 树洞联网搜索（Kimi 内置 $web_search，按次计费）：on（默认）= 开，off = 关；
    # 只在生效厂商是 Kimi（moonshot）时真正下发，其他厂商自动忽略
    TREEHOLE_WEB_SEARCH: str = os.getenv("TREEHOLE_WEB_SEARCH", "on")

    # LLM 采样温度（空 = 默认 0.7）：Kimi k3 等推理模型只接受 temperature=1，用这类模型时设 1
    LLM_TEMPERATURE: str = os.getenv("LLM_TEMPERATURE", "")

    def treehole_llm(self) -> tuple[str, str, str]:
        """树洞生效的 (api_key, base_url, model)：TREEHOLE_API_KEY 非空走 Kimi 组（带默认值），
        否则整体回退 LLM_* 组。"""
        if self.TREEHOLE_API_KEY:
            return (
                self.TREEHOLE_API_KEY,
                self.TREEHOLE_BASE_URL or "https://api.moonshot.cn/v1",
                self.TREEHOLE_MODEL or "kimi-k2.6",
            )
        return (self.LLM_API_KEY, self.LLM_BASE_URL, self.LLM_MODEL)

    @property
    def treehole_web_search_enabled(self) -> bool:
        """树洞联网开关：TREEHOLE_WEB_SEARCH=on（默认）且生效厂商是 Kimi 才启用。
        Kimi 系入口：platform 的 moonshot.cn 与编程套餐的 kimi.com/coding 都算。"""
        _, base_url, _ = self.treehole_llm()
        kimi_hosted = "moonshot" in base_url or "kimi.com" in base_url
        return self.TREEHOLE_WEB_SEARCH.strip().lower() == "on" and kimi_hosted

    # Redis（可选，连不上自动降级进程内字典）
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # SQLite
    DB_PATH: str = os.getenv("DB_PATH", str(_SERVER_DIR / "data" / "app.db"))

    # Web Push（第 5 期）：VAPID claims 的联系方式，规范要求 mailto: 或 https:
    VAPID_SUB: str = os.getenv("VAPID_SUB", "mailto:us-app@localhost")

    @property
    def vision_enabled(self) -> bool:
        """视觉开关：配了 key（含 LLM 回退）且配了 VISION_MODEL 才启用。"""
        return bool(self.VISION_API_KEY and self.VISION_MODEL)

    @property
    def web_search_enabled(self) -> bool:
        """联网搜索开关：LLM_WEB_SEARCH=on 才启用（默认 off = 不联网降级）。"""
        return self.LLM_WEB_SEARCH.strip().lower() == "on"

    def vision_reasoning(self, scene: str) -> str:
        """分场景思考强度：场景变量 → 总开关 VISION_REASONING → 场景默认值。

        scene ∈ caption/receipt/food。caption 要精准默认 high，food 只识别+估分量
        默认 low，receipt 抢速度默认 minimal。
        """
        per_scene = {
            "caption": self.VISION_REASONING_CAPTION,
            "receipt": self.VISION_REASONING_RECEIPT,
            "food": self.VISION_REASONING_FOOD,
        }
        defaults = {"caption": "high", "receipt": "minimal", "food": "low"}
        return per_scene.get(scene, "") or self.VISION_REASONING or defaults.get(scene, "")

    @property
    def upload_dir(self) -> Path:
        """图片上传目录：跟随 DB_PATH 所在目录（测试库指向临时目录时自动隔离）。"""
        return Path(self.DB_PATH).parent / "uploads"


settings = Settings()
