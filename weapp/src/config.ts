/**
 * 小程序端 API base。**必须与网页端指向同一个 FastAPI 后端**：
 * 两端共用同一套 API、同一个 SQLite 数据库，数据天然一致、实时同步。
 */
export const API_BASE =
  process.env.NODE_ENV === 'production'
    ? // TODO 上线前替换为已备案的 HTTPS 域名（小程序 request 合法域名要求 HTTPS + ICP 备案）
      'https://api.example.com'
    : 'http://localhost:8000'
