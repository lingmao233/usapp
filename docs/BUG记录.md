# BUG 记录

> 用途：每次线上/本地出现的故障都按统一格式登记在这里，方便回溯与复盘。
> 填写时机：bug 修复并验证通过后，由修复者（人或 AI）补登。

## 填写格式

每条记录包含五节，顺序固定：

```markdown
## BUG-编号 一句话标题

- **日期**：YYYY-MM-DD
- **环境**：本机 dev / 腾讯云生产 / 两端
- **现象**：用户可感知的表现（报错文案、页面状态），附关键日志摘录
- **根因**：一句话结论 + 逐步推理（代码引用用 `路径:行号`）
- **修复**：改了哪些文件、每个改动解决什么
- **验证**：跑过哪些测试/命令，结果如何
- **预防**：同类问题以后怎么避免或更快定位
```

---

## BUG-001 蒸馏长事务导致并发写 database is locked

- **日期**：2026-08-13
- **环境**：腾讯云生产（手机端复现）
- **现象**：手机端加愿望等待数秒后提示「加愿望失败，再试一次」，愿望列表一度显示为空（疑似数据丢失）。日志出现 `sqlite3.OperationalError: database is locked`（`tasks.py` 落 task_runs 时）。
- **根因**：记忆蒸馏 `refresh_dirty` 从 `_ensure_rows` 的第一条写入起持有写事务，直到全部 LLM 调用结束才提交（`memory.py` 原实现只在函数末尾 `conn.commit()` 一次）。蒸馏含 5 次 DeepSeek 调用、每次约 10 秒，写锁持有约 80 秒；SQLite 默认 busy_timeout 仅 5 秒，期间任何并发写（加愿望的 INSERT、任务层写 task_runs）超时报错 500。愿望列表为空是前端加载请求失败后停留在初始空态，**数据未丢失**（直查库中 wishes 行数确认）。
- **触发链**：有人打开周报页 → 滚动刷新触发 `generate_report` → 先跑 `refresh_dirty`（长事务）→ 此时任何写入失败。
- **修复**：
  - `server/app/db/database.py`：`get_conn` 增加 `PRAGMA busy_timeout=30000`，并发写等锁上限 5s → 30s
  - `server/app/services/memory.py`：`refresh_dirty` 改为每行 UPDATE 后立即 commit，写锁从分钟级缩到毫秒级；每行自带 `dirty=0`，逐行提交幂等可续跑
- **验证**：pytest 72/72 通过；smoke 48 断言通过。
- **预防**：凡是「DB 写入 + LLM 调用」混合的函数，写事务绝不跨 LLM 调用；新增管线函数时检查事务边界。

## BUG-002 向量维度混存（1024/2048）导致共同愿望与周报 500

- **日期**：2026-08-13
- **环境**：本机 dev（生产库同法检查，未见异常则无需处理）
- **现象**：`GET /api/wishes/common` 与周报生成持续 500，日志 `ValueError: shapes (1024,) and (2048,) not aligned`。
- **根因**：本机 `.env` 的 `DOUBAO_EMBED_DIM` 曾设为 2048（或换过 embedding 模型/端点），该时期写入的 wishes 2 条、knowledge_items 1 条为 2048 维；配置改回 1024 后新数据为 1024 维，新旧混存。余弦相似度要求两向量等长，跨维度比较必抛异常。定位方法：`SELECT LENGTH(embedding)/4, COUNT(*) FROM <表> GROUP BY 1`，fragments 全 1024、wishes 混 1024+2048、knowledge_items 混 1024+2048。
- **修复**：
  - `server/app/db/database.py`：`cosine()` 增加维度守卫，维度不一致按不相似返回 0.0（一处修复覆盖共同愿望/语义分量/相关推荐/知识搜索全部调用方）
  - `server/scripts/reembed.py`：回填范围从仅 fragments 扩到 wishes、knowledge_items，公式与各写入路径严格同口径
  - 本机数据：执行 `.venv-mac/bin/python scripts/reembed.py`，13 条向量全部统一为 1024 维
- **验证**：回填后三张表 `GROUP BY dim` 均只剩 1024；pytest 72/72；smoke 48 断言通过。
- **预防**：**改 `DOUBAO_EMBED_DIM` / 换 embedding 模型后必须立刻跑 `scripts/reembed.py`**（服务器上：`docker compose exec app python scripts/reembed.py`）；维度混存时功能不再崩溃，但混存行的相似度按 0 处理，仍需回填恢复。

## BUG-003 手动愿望提交慢（同步 AI）+ 愿望页列表偶发"消失"

- **日期**：2026-08-13
- **环境**：本机 dev（代码问题两端同在）
- **现象**：加愿望要数秒才返回；切到其他页面再回来，愿望列表显示为空（再点一次恢复），共同愿望区块也不再出现。
- **根因（两条独立原因）**：
  1. 慢：`add_wish`（`services/wishes.py`）在请求内**同步串行**调 DeepSeek 分类 + 豆包向量（各数秒）才入库返回；碎片当年就是秒回 + 后台异步管线，愿望没跟上。
  2. 列表消失：`Wishes.tsx` 的 `load()` 用 `Promise.all` 把 `listWishes` 与 `commonWishes` 绑成一体，共同愿望接口当时因 BUG-002 持续 500 → `Promise.all` 整体 reject → 唯一 catch 静默吞掉 → `setWishes` 从未执行 → 页面停在初始空态，看似"数据丢了"。「共同愿望消失」同理（500 期间永不渲染）。
- **修复**：
  - `server/app/services/wishes.py`：`add_wish` 改快路径——校验后立即入库返回（category 默认 `do`、embedding 留 NULL）；新增 `process_wish` 后台任务做分类 + 向量化（经任务层，重试幂等）。`compute_common_wishes` / `generate_plan` 本就跳过 NULL embedding，向量就绪后自动参与匹配
  - `server/app/api/wishes.py`：POST 挂 `BackgroundTasks` 调 `process_wish`
  - `src/pages/Wishes.tsx`：`load()` 拆成两个独立 try/catch（共同愿望失败不再拖垮列表）；加 `loaded` 加载态，空态文案只在加载完成后展示
- **验证**：pytest 72/72；smoke 48 断言；`npm run build`；`weapp npx tsc --noEmit`。
- **预防**：新增涉及 AI 调用的写接口默认走「秒回 + 后台管线」；前端并列请求禁止共享失败路径（`Promise.all` + 单一 catch 在本项目视为反模式），各请求独立兜底。

## BUG-004 生成方案把愿望踢出共同愿望匹配池

- **日期**：2026-08-13
- **环境**：本机 dev（代码问题两端同在）
- **现象**：共同愿望正常显示，但点「生成方案」后共同愿望区块整个消失（匹配余弦实为 1.0，并非算法问题）。
- **根因**：`generate_plan` 生成方案后顺手把愿望 `status` 改成 `matched`（`services/wishes.py` 原实现），而 `compute_common_wishes` 只捞 `status='active'` 的愿望 → 参与人一退出池子，跨用户配对凑不齐，共同愿望消失。「matched」语义把"已生成方案"与"不再参与匹配"错误绑定。
- **修复**：
  - `generate_plan` 只缓存方案 JSON（含 participants），不再改 status；`matched` 语义整体下线，存量数据由 `_migrate_wishes_matched_status` 迁回 `active`
  - 匹配制度改为用户驱动：新增 `PUT /api/wishes/{id}/done` 勾选完成（可逆），完成的愿望（`status='done'`）才移出匹配池；前端「我的愿望」分未完成/已完成（默认折叠）两区块
  - 接口契约同步变更：POST `/api/wishes/{id}/plan` 有缓存直返、无缓存转后台异步生成（`{"status":"generating"}`）+ Web Push 完成通知，前端轮询兜底；两处旧测试与 smoke 第 7 节适配新契约
- **验证**：新增 `tests/test_plans.py` 5 用例（勾选剔除/回归/缓存指纹/预生成/追问流）；pytest 77/77；smoke 48 断言。
- **预防**：状态机变更要排查所有读取方（匹配池、统计、列表筛选）；「完成」这类用户意图不应由系统自动动作代劳。

## BUG-005 周报把「事实与猜测的分寸」写作纪律当成正文章节渲染

- **日期**：2026-08-14
- **环境**：腾讯云生产（真实 key 生成的周报）
- **现象**：周报正文出现「⚠️ 事实与猜测的分寸」一节，AI 用第一人称向读者解释自己的分寸规则（"别问 TA 沙滩烫不烫""不保证温柔，但保证不编料"），人机感重。
- **根因**：`WEEKLY_REPORT_PROMPT`（`server/app/ai/prompts.py`）里「事实与猜测的分寸」规则块被夹在「请生成 Markdown 格式的报告，包含以下结构：」与报告章节骨架之间，LLM 把它误判为需要渲染的章节之一。规则本意是约束措辞的内部纪律，不该出现在输出里。
- **修复**：`prompts.py` 周报模板重排——规则块改标为「写作纪律（只约束你的措辞，不是报告内容，绝不要写进报告）」，置于结构说明之前；章节骨架前明确「报告只包含以下章节（不要自加章节，分寸说明、免责声明、写作纪律之类的一律不写）」。四条分寸规则原文未动。
- **验证**：`test_persona.py` 补断言「绝不要写进报告」进 prompt；pytest 82/82。mock 桩不读模板，最终效果需真实 key 手测确认（POST /api/reports/generate 强刷）。
- **预防**：prompt 模板里「给模型的指令」与「要渲染的输出结构」必须分区写明、禁止混排；新增模板段落时检查占位符上下文是否会被误读为输出章节。

## BUG-006 高德强制绑定安全密钥后签名缺失，方案真实数据静默失效

- **日期**：2026-08-14
- **环境**：两端（代码问题，外部政策变更触发）
- **现象**：高德开放平台新建 Web 服务 key 强制绑定安全密钥，绑定后不带 `sig` 的请求被拒（`INVALID_USER_SIGNATURE`）。`_get` 记 warning 返回 None，方案静默回退纯 LLM 经验推荐——用户无报错，但 POI/天气/通勤不再是真实数据。
- **根因**：`server/app/services/amap.py` 的 `_get` 只传 `key` 参数，从未实现数字签名；高德新政使该路径必然失败。
- **修复**：
  - `amap.py`：新增 `_sig`（除 sig 外全部参数含 key 按名升序 `key=value&` 连接，末尾拼私钥后 MD5 小写）；`_get` 在 `AMAP_SECRET` 非空时自动带 sig，空则维持老行为
  - `server/app/config.py`：新增 `AMAP_SECRET` 配置项（默认空）
  - `.env.example`：补 `AMAP_SECRET` 说明（新 key 必填，未绑密钥的老 key 留空）
- **验证**：新增 `tests/test_amap.py` 3 用例（含已知答案向量 md5("address=北京&key=testkey"+"testsecret") 锁定算法）；pytest 85/85。真实 key 联调待手测：curl 带 sig 请求返回 `"status":"1"` 即通。
- **预防**：外部服务封装层的「失败静默回退」意味着政策/配额类故障无感知——手测新接入的外部 key 时用 curl 直验，不靠前端表现猜。

