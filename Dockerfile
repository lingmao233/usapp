# ---------- 第一阶段：构建前端 ----------
FROM node:20 AS web-builder
WORKDIR /build

# 先拷依赖清单，利用构建缓存
COPY package.json package-lock.json ./
RUN npm ci

COPY index.html vite.config.ts tsconfig.json tsconfig.app.json tsconfig.node.json \
     tailwind.config.js postcss.config.js components.json ./
COPY src ./src
# PWA 静态资产（manifest / sw.js / 图标，第 5 期）：vite 原样拷进 dist
COPY public ./public
RUN npm run build

# ---------- 第二阶段：Python 运行时 ----------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 装后端依赖（requirements.txt 单独拷贝，便于缓存）
COPY server/requirements.txt ./server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

# 后端代码 + 前端构建产物
COPY server ./server
COPY --from=web-builder /build/dist ./dist

# SQLite 数据目录（compose 挂 named volume 到这里持久化）
RUN mkdir -p /app/server/data

WORKDIR /app/server
EXPOSE 8000

# 生产模式：单容器托管 API + 前端静态文件
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
