"""「我们」后端入口：FastAPI，端口 8000。

生产模式下同时托管前端 dist/ 静态文件（SPA 回退到 index.html）。
/api 路由先于静态挂载注册，不会被盖住。
"""
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from . import ai
from .api import accounts, auth, chat, circles, fragments, goals, knowledge, ledger, plans, push, reports, self as self_api, uploads, wishes
from .db.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="我们 · API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(accounts.router)
app.include_router(auth.router)
app.include_router(self_api.router)
app.include_router(circles.router)
app.include_router(fragments.router)
app.include_router(knowledge.router)
app.include_router(wishes.router)
app.include_router(reports.router)
app.include_router(push.router)
app.include_router(uploads.router)
app.include_router(chat.router)
app.include_router(goals.router)
app.include_router(goals.blocks_router)
app.include_router(plans.router)
app.include_router(ledger.router)
app.include_router(ledger.calories_router)


@app.on_event("startup")
def startup() -> None:
    init_db()
    uploads.upload_dir()  # 启动时确保图片上传目录存在
    logging.getLogger("us.main").info("AI 模式：%s", ai.mode())


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "ai_mode": ai.mode()}


# ---------- 生产模式：托管前端构建产物（SPA） ----------

class SPAStaticFiles(StaticFiles):
    """静态文件找不到时回退到 index.html（前端路由由 SPA 接管）。"""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


def _find_dist() -> Path | None:
    server_dir = Path(__file__).resolve().parent.parent
    for candidate in (server_dir.parent / "dist", server_dir / "dist"):
        if (candidate / "index.html").exists():
            return candidate
    return None


_DIST_DIR = _find_dist()
if _DIST_DIR is not None:
    # 注册顺序保证 /api 路由优先，静态挂载只接住其余路径
    app.mount("/", SPAStaticFiles(directory=_DIST_DIR, html=True), name="spa")
    logging.getLogger("us.main").info("已挂载前端静态文件：%s", _DIST_DIR)
else:
    logging.getLogger("us.main").info("未发现 dist/，仅提供 API（开发模式由 vite 托管前端）")
