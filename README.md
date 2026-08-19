# 我们

面向 3-14 人熟人小圈子的 AI 异步社交 App（web + PWA）：各自随手丢碎片（文字/链接/图片），AI 自动分类打标签、发现成员间的隐性连接（「可能相关」卡片）、把链接归档进知识库并生成摘要、识别愿望并匹配共同愿望，每周生成「交集报告」。一个身份（account）可以加入多个圈子，首页的「我的圈子」列表随时切换进入。

**不需要 Redis 也能一键跑起来**（可选缓存自动降级进程内字典）；AI 能力需要配置 LLM/EMBEDDING/VISION key（三组参数同厂商只需填一个 LLM_API_KEY）。

## 功能一览

- **碎片墙**：文字/链接/图片碎片，公开或仅自己可见；AI 自动分类、打标签、向量化，图片经多模态向量与文字同空间检索
- **可能相关**：碎片下方出现跨成员的语义相似卡片
- **知识库**：链接自动归档 + 正文提取 + AI 摘要，标签筛选 + 语义搜索
- **愿望清单**：「想去/想学/想吃」自动识别，匹配共同愿望并一键生成行动方案
- **每周交集报告**：懒触发 + 周内滚动刷新；圈内公开语录作为 few-shot 注入，口吻由「圈子人格」决定（5 套预设 + 自定义，任何成员可换，圈与圈互不影响）
- **记忆层与关系图**：每晚离线蒸馏个人画像（含说话风格维度）与成员对亲密度（语义/互动/共同愿望/共同主题四分量），SVG 关系图按观看者身份做服务端过滤
- **互动**：公开碎片可评论（楼中楼）、点赞，作者可收 Web Push 通知
- **多圈子多身份**：一个身份加入多个圈子；6 位恢复码（可自定义）换设备找回身份
- **PWA**：可安装到主屏，手写 manifest + service worker，支持 Web Push
- **个人功能（「我的」Tab）**：目标系统（减肥/存款/学习 + 自定义）+ AI 每日计划（昨日完成自适应）+ 拍照记账/热量估算（视觉模型识别 + 确认入账）+ 三类型联动规则；数据账号级私有，可选公开到指定圈子接受熟人鞭策
- **情绪树洞**：私密陪伴型对话 agent（Landing 第三入口）——记住你说过的和发过的，按你的偏好与设立的人设回应

## Agent 架构

两个 agent 场景共用 **LangGraph** 图编排（白名单依赖：langgraph / langmem / langgraph-checkpoint-sqlite / langchain-openai）：

**情绪树洞（有状态对话 agent）**

```
用户消息 → 意图路由（倾诉/提问/查数据）
        → 检索（查询改写 → 碎片向量+关键词双路 RRF 融合 × 时效衰减）
        → 工具节点（tool calling：查账本/今日计划/热量/碎片/记忆画像）
        → 人设扮演生成（酒馆式人设卡 + 偏好画像 + 引用落地带来源）
        → 安全护栏（仅强烈自伤意愿替换干预话术，普通抱怨不误伤）
        → 记忆写回（L1 原子记忆实时抽取，langmem）
```

- 分层记忆（借鉴 TencentDB-Agent-Memory 的 L0-L3 模型）：L0 对话原文永不删 → L1 原子事实实时抽取 → L2 场景聚类 → L3 复用夜间蒸馏画像
- 上下文压缩：人设卡/画像/最近 10 轮原文永不压缩；更早历史走填槽式滚动增量摘要（带源消息 id 可回溯）
- 会话状态：LangGraph SqliteSaver checkpoint 落同一个 SQLite
- 评估闭环：`server/scripts/treehole_eval.py`（事实保留率/检索命中率/护栏正确率/人设一致率 + LLM 裁判 pairwise），改 prompt/检索后跑回归

**方案评审团（多 agent 协作，backlog）**：愿望方案生成改为 fan-out/fan-in——起草人格出草案 → PLAN_KINDS 多个人格并行评审 → 汇总修订终稿。

**选型取舍**：记忆持久化用自研 SQLite BaseStore 适配（langmem 官方只有内存/Postgres store）；不上向量数据库——几千条碎片暴力余弦毫秒级，向量 DB 解决的是百万级 ANN 规模问题而非质量问题，检索层已抽象，十万级再切 pgvector 不改业务。

## 技术栈

- 前端：React 19 + TypeScript + Vite + Tailwind + shadcn/ui（web + PWA，端口 7100）
- 后端：FastAPI（端口 8000），同进程托管前端构建产物；前端经 Vite proxy `/api` 访问
- 存储：SQLite（WAL，`server/data/app.db`，手写 PRAGMA 迁移）+ Redis（可选，连不上自动降级进程内缓存）
- AI：LLM/EMBEDDING/VISION 三组通用参数（OpenAI 兼容，厂商可换）；未配置的功能自动优雅关闭（如视觉），不静默返回假数据
- 向量检索：embedding 以 float32 blob 存 SQLite，numpy 暴力余弦（小圈子规模足够）
- 推送：pywebpush（VAPID 密钥首次使用自动生成，订阅存 SQLite）

## 快速开始

### 1. 创建 Python 虚拟环境并安装依赖

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. 配置环境变量（可选）

```bash
cp .env.example server/.env
```

不接真实模型可留空跳过（AI 功能将不可用）；要接真实模型时填写（三组参数同厂商时只需填 `LLM_API_KEY`，其余 KEY/BASE_URL 自动回退）：

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` | 文本 LLM（OpenAI 兼容 chat）API key，用于分类/摘要/周报/画像/方案 |
| `LLM_BASE_URL` / `LLM_MODEL` | OpenAI 兼容端点与模型名（如阿里百炼 `https://dashscope.aliyuncs.com/compatible-mode/v1` + `qwen-plus`） |
| `EMBEDDING_MODEL` | 文本向量模型名（如 `text-embedding-v4`）；`EMBEDDING_API_KEY`/`EMBEDDING_BASE_URL` 留空回退 LLM 组 |
| `VISION_MODEL` | 可选；视觉模型名（如 `qwen-vl-max-latest`），用于图片 caption/账单/食物识别，留空自动跳过 |
| `REDIS_URL` | 可选；连不上时降级为进程内字典并打 warning |

base URL 与模型名均有回退与默认值，按需覆盖，详见 `.env.example`。

### 3. 启动

```bash
npm install   # 首次
npm run dev
```

一条命令同时拉起后端（uvicorn :8000）和前端（vite :7100），Ctrl+C 两个进程一起退出。
换端口：`npm run dev -- --port 7200`（参数会转发给 vite）。

打开 http://localhost:7100 即可。API 文档在 http://localhost:8000/docs 。

### 4. 建圈子拉朋友内测

1. 打开首页 →「建一个新圈子」，填圈子名 + 昵称（可顺手选个圈子人格），得到一个 6 位邀请码
2. 把邀请码发给朋友，朋友打开同一地址选「我有邀请码，加入圈子」
3. 各自随手丢碎片：一句话、一个链接、一张图、一个想做的事
4. 碎片下方会出现 mint 色的「可能相关」卡片；链接自动进知识库；「想去/想学」自动进愿望清单；周一首次打开自动生成本周交集报告
5. 顶栏「圈子人格」随时给圈子换口吻，下一次周报生效

## 验证

```bash
# 单元/集成测试（200+ 用例，确定性桩在 tests/fakes.py，conftest 自动接线，不触网）
cd server && .venv/bin/python -m pytest tests/ -q

# 手写全链路冒烟（62 断言：建圈→加入→发碎片→分类/归档/愿望→相关推荐→语义搜索→共同愿望→方案→周报→目标/计划/记账/热量/鞭策）
.venv/bin/python scripts/smoke_test.py

# 前端构建
npm run build
```

## 目录结构

```
us-app/
├── scripts/dev.sh        # 一键启动前后端
├── server/               # FastAPI 后端
│   ├── app/api/          # 路由
│   ├── app/services/     # 业务逻辑 + 异步管线 + 任务层（重试/degraded 落库）
│   ├── app/ai/           # llm / embedding / vision provider（OpenAI 兼容 HTTP）+ prompts + langmem 集成
│   ├── app/db/           # sqlite schema、redis 缓存（可降级）
│   ├── scripts/          # smoke_test.py 等脚本
│   └── tests/            # pytest 200+ 用例（fakes.py 确定性桩）
├── src/                  # React 前端（web + PWA）
│   ├── core/             # 平台无关核心：types / api 工厂 / storage 抽象
│   ├── pages/            # 碎片墙 / 知识库 / 愿望清单 / 关系图 / 入圈
│   └── lib/              # web 组装壳（fetch + localStorage）、推送、图片压缩
├── deploy/               # 一键装机 / nginx 参考 / 每日备份
└── docs/                 # 设计文档与部署指南
```

## 部署上线

**推荐一键脚本**：上传代码到服务器后执行 `bash deploy/setup.sh`（幂等可重跑，`--yes` 非交互）——自动装 Docker、交互式配 `.env`、`docker compose up -d --build`、健康检查并打印访问地址，约 10 分钟上线。

完整步骤见 **[docs/部署指南.md](docs/部署指南.md)**（快速路径 + 手动备选 + HTTPS + 备案提醒 + 日常维护）。

- `Dockerfile`：多阶段构建，单容器同时托管 API 与前端静态文件
- `docker-compose.yml`：app + redis 两服务，SQLite 数据挂 named volume 持久化
- `deploy/setup.sh`：一键部署脚本（推荐）
- `deploy/nginx.conf`：可选的反代/HTTPS 参考配置
- `deploy/backup.sh`：SQLite 每日备份（保留 14 份）

## 已知限制

- 认证从简：后端信任前端传入的 circle_id/user_id，仅适合熟人小圈子内测
- 部分网站反爬时，知识条目降级为只存标题/URL
