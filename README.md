# 我们

面向 3-14 人小圈子的 AI 异步社交 App：各自随手丢碎片（文字/链接），AI 自动分类、发现成员间的隐性连接（"可能相关"卡片）、归档知识库并生成摘要、识别愿望并匹配共同愿望，每周生成「交集报告」。一个身份（account）可以加入多个圈子，首页的「我的圈子」列表随时切换进入。

**没有 API key、没有 Redis 也能一键跑起来**——AI 会自动进入 mock 模式（本地确定性桩），完整体验所有功能。

## 技术栈

- 前端：React + TypeScript + Vite + Tailwind + shadcn/ui（端口 7100）
- 小程序端：Taro 4 + React + tailwindcss（`weapp/`，编译到微信小程序）
- 双端共用：`src/core/` 平台无关核心（类型 / API 客户端 / 存储抽象），两端注入各自的 storage 与 HTTP 实现
- 后端：FastAPI（端口 8000），前端经 Vite proxy `/api` 访问
- 存储：SQLite（`server/data/app.db`）+ Redis（可选，连不上自动降级进程内缓存）
- LLM：DeepSeek API（OpenAI 兼容，httpx 直连）
- Embedding：豆包 doubao-embedding-vision（火山方舟 OpenAI 兼容接口）
- 向量检索：embedding 以 float32 blob 存 SQLite，numpy 暴力余弦（小圈子规模足够）

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

不填任何 key 直接跳过本步也能跑（mock 模式）。要接真实模型时填写：

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API key，用于分类/摘要/周报/方案 |
| `DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com` |
| `DOUBAO_API_KEY` | 火山方舟 API key，用于 embedding |
| `DOUBAO_BASE_URL` | 默认 `https://ark.cn-beijing.volces.com/api/v3` |
| `DOUBAO_EMBEDDING_MODEL` | 默认 `doubao-embedding-vision-250615` |
| `REDIS_URL` | 可选；连不上时降级为进程内字典并打 warning |

### 3. 启动

```bash
npm install   # 首次
npm run dev
```

一条命令同时拉起后端（uvicorn :8000）和前端（vite :7100），Ctrl+C 两个进程一起退出。
换端口：`npm run dev -- --port 7200`（参数会转发给 vite）。

打开 http://localhost:7100 即可。API 文档在 http://localhost:8000/docs 。

### 4. 建圈子拉朋友内测

1. 打开首页 →「建一个新圈子」，填圈子名 + 昵称，得到一个 6 位邀请码
2. 把邀请码发给朋友，朋友打开同一地址选「我有邀请码，加入圈子」
3. 各自随手丢碎片：一句话、一个链接、一个想做的事
4. 碎片下方会出现 mint 色的「可能相关」卡片；链接自动进知识库；「想去/想学」自动进愿望清单；周一首次打开自动生成本周交集报告

## 验证

```bash
# 冒烟测试（mock 模式完整链路：建圈→加入→发碎片→分类/归档/愿望→相关推荐→语义搜索→共同愿望→方案→周报）
server/.venv/bin/python server/scripts/smoke_test.py

# 前端构建
npm run build
```

## 目录结构

```
us-app/
├── scripts/dev.sh        # 一键启动前后端
├── server/               # FastAPI 后端
│   ├── app/api/          # 路由
│   ├── app/services/     # 业务逻辑 + 异步管线
│   ├── app/ai/           # deepseek / doubao / mock 桩（统一接口）
│   ├── app/db/           # sqlite schema、redis 缓存（可降级）
│   └── scripts/smoke_test.py
├── src/                  # React 前端（网页端）
│   ├── core/             # 平台无关核心：types / api 工厂 / storage 抽象（双端共用）
│   ├── pages/            # 碎片墙 / 知识库 / 愿望清单 / 入圈
│   └── lib/api.ts        # web 组装壳：注入 fetch + localStorage
└── weapp/                # 小程序端（Taro 4，编译产物在 weapp/dist）
    └── src/platform.ts   # 小程序组装壳：注入 Taro.request + Taro storage
```

## 双端说明（网页 + 小程序）

**数据一致**：小程序与网页端调用**同一个 FastAPI 后端、同一个 SQLite 数据库**（`weapp/src/config.ts` 的 `API_BASE` 指向同一地址），无小程序独立存储，数据天然一致、实时同步。

**账号打通（已定决策）**：openid 与 account 一对多绑定。网页端老用户在小程序里输入恢复码即可认领同一身份（入圈页「换了设备？用恢复码找回身份」，claim 流程与网页端一致）；后续后端新增 `/api/auth/wechat` 后，小程序启动时 `wx.login` 自动绑定 openid，新用户免恢复码。

**小程序开发**：

```bash
cd weapp
npm install          # 首次
npm run dev:weapp    # 监听编译到 dist/
```

1. 同步启动本地后端：`npm run dev`（或单独 `cd server && .venv/bin/uvicorn app.main:app --port 8000`）
2. 微信开发者工具打开 `weapp/` 目录，开发期在「详情 → 本地设置」勾选**不校验合法域名**，即可连 `http://localhost:8000`
3. `project.config.json` 的 `appid` 目前是测试号占位，注册小程序后替换为自己的 AppID
4. 生产环境：`weapp/src/config.ts` 把 `API_BASE` 换成**已备案的 HTTPS 域名**，并配置到小程序 request 合法域名

**网页端响应式**：桌面端布局不变；窄屏（<640px）顶栏的「身份码/切换圈子/换个身份」收进右上角菜单，三个 tab 保持在顶栏。

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
- 图片碎片未做（仅文字 + 链接）
- 部分网站反爬时，知识条目降级为只存标题/URL
