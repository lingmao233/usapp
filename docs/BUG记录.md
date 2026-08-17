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

## BUG-007 方案把人名当店名让大家去高德搜 + 单模板只会写"一起去"

- **日期**：2026-08-14
- **环境**：两端（真实 key 下生成质量缺陷）
- **现象**：愿望「想找欧培昇玩」（人名）生成的方案让用户"用高德地图搜索'欧培昇'确定具体地址"；且无论愿望是什么，方案都是同一个"一起去"出行模板，无真实数据时步骤仍围绕地图搜索展开。
- **根因**（两条）：
  1. `PLAN_EXTRACT_PROMPT` 只做"提取 city/keywords"，不判断愿望类型——人名被当 POI 关键词送高德，搜不到 → `real_data` 为空。
  2. `PLAN_PROMPT` 规则只覆盖"有真实数据怎么采信"，没约束"没有真实数据时不得把愿望原文词当店名/地名"，LLM 自由发挥成"去地图搜一下 XX"。
- **修复**：
  - `PLAN_EXTRACT_PROMPT` 升级为愿望分析：输出 `kind`（六选一枚举：outing 出行/learning 学习/dining 聚餐/activity 活动购物/meet 约人/venting 情绪倾诉）+ `scene` + `city`/`keywords` + `need_real_data`；meet/venting 强制不查高德
  - `prompts.py` 新增 `PLAN_KINDS`：每种类型**写死实现路径要点**（如 venting=一本正经玩梗逗开心（含示例梗）、收尾落真实小行动、绝不复述被吐槽的人名、不拿别人开涮；meet=围绕怎么约/凑时间/选活动）——LLM 只选类型，不许自己发明实现路径
  - `wishes.py` `_real_plan_context` → `_plan_context`：返回 `(analysis, real_data)`，`need_real_data=False`、无 keywords 或未配 key 时不查高德
  - `ai.build_plan_prompt` 纯函数按 kind 注入路径要点（非法 kind 回退 activity）；`PLAN_PROMPT` 规则覆盖有/无真实数据两分支，无数据时严禁把愿望原文词（尤其人名）当店名
  - 类型人格（2026-08-14 共识）：`PLAN_KIND_PERSONAS` 六类人格写死（方案=类型人格，周报=圈人格）；分析步加 `mood` 枚举，venting 双形态——消极情绪换树洞人设不玩梗，非消极走损友玩梗（约束清单见 `docs/方案生成-类型人格与约束.md`）
  - `ai.extract_plan_query` 解析新字段（need_real_data 防御字符串布尔、kind 校验枚举）；mock 桩确定性产出（activity + 恒 True 维持旧行为）
- **验证**：`tests/test_amap.py` 8 用例（签名 3 + 上下文分支 3 + 纯函数路径/双形态断言 1 + 真实路径解析 1）；pytest 91/91；smoke 48 断言。生成效果需真实 key 手测（样例集见约束文档 §6）。
- **预防**：LLM 结构化输出的枚举/布尔字段要做防御解析；prompt 规则要同时覆盖"有数据"与"无数据"两个分支；**愿望类型用枚举收敛 + 服务端写死实现路径**，自由文本类型描述会被 LLM 自由发挥。另：高德 key 必须建「Web 服务」类型（JS API 类型报 USERKEY_PLAT_NOMATCH），本机已踩过。

## BUG-008 共同愿望区块三连环：重算清空、DeepSeek 超时、建议无人格

- **日期**：2026-08-14
- **环境**：本机 dev（真实 key）
- **现象**：① 加新愿望后共同愿望区块长时间空白（旧结果不可用）；② 日志 `DeepSeek 愿望匹配失败，回退仅用相似度：The read operation timed out`；③ 「想暴富」的共同愿望建议写成"线上理财规划课程、基金定投"式的正经文案，无类型人格。
- **根因**（三条独立）：
  1. `GET /api/wishes/common` 指纹未命中时**同步**跑 embedding 聚类 + LLM 确认（最长 60s+），请求期间前端只有空态可显示。
  2. `deepseek.chat_json` 无 timeout 参数，默认 60s 对多候选的 WISH_MATCH_PROMPT 不够，read timeout 后回退相似度（degraded 已落 task_runs，属实非故障）。
  3. **用户看到的"方案感"文案其实是共同愿望卡片的 `suggestion` 字段**，由 `WISH_MATCH_PROMPT` 生成——该 prompt 自 PRD 以来从未接入类型人格体系（BUG-007 的人格只接到了 PLAN_PROMPT 的方案路径），所以"想暴富"被当成正经理财需求出建议。task_runs 无任何 wish_plan 记录、wish.plan 为空可证方案路径根本没被点过。
- **修复**：
  - `wishes.py`：`common_wishes` 改 **stale-while-revalidate**——指纹变先返回旧结果 + `refreshing` 标记（旗标防抖 120s 窗口），新增 `refresh_common_wishes` 后台重算写缓存；`api/wishes.py` 路由按 "trigger" 放 BackgroundTasks；`nightly._pregen_plans` 批处理场景检测过期则同步重算
  - `Wishes.tsx`：新增 `refreshCommon` 轮询（陈旧结果先上屏，3s 轮询收敛后一次性换新），空态加"AI 正在发现共同愿望…"；`src/core/api.ts` 响应类型加 `refreshing?`
  - `deepseek.chat_json` 加 `timeout` 参数，`confirm_common_wishes` 放宽到 120s
  - `WISH_MATCH_PROMPT` 补分类型出招指引（出行/学习/吃喝/购物/约人/玩梗/消极情绪七类），消极情绪不玩梗先接住情绪，人名/对抗性建议禁令与方案护栏对齐
  - 适配：test_plans `_common` 与 smoke 第 6 节改轮询式；3 处直调测试（test_memory/test_visibility×2）同步适配
- **验证**：新增 stale 直返断言 + WISH_MATCH_PROMPT 指引断言；pytest 93/93；smoke 48 断言；`npm run build` + weapp tsc 通过；本机 `common_wishes_cache` 已清，下次访问按新 prompt 重算。真实 key 手测待用户确认"想暴富"建议变玩梗口吻。
- **预防**：**同一用户感知区有多条生成路径时（方案 plan / 建议 suggestion），人格与护栏改造要逐路径排查**，不能假设只此一条；慢 LLM 调用一律不许堵在请求路径上（ stale-while-revalidate 或后台任务 + push）；外部 API 超时参数要显式可配。

## BUG-009 找回面板：输入法回车选词误触发查询 + 旧结果残留

- **日期**：2026-08-14
- **环境**：本机 dev（前端交互缺陷，两端同在）
- **现象**：按名字找回身份码时，输入名字后直接显示"没有找到叫这个名字的成员"，再点一次「查一下」又能正常出结果。
- **根因**（两条叠加）：
  1. 名字输入框的 `onKeyDown` 未做 IME 守卫——中文输入法**回车确认候选词**时 `keydown Enter` 已触发 `handleLookup`，拿半截拼音/未完成的词去查，返回空列表（`Onboarding.tsx`）。
  2. 查询结果（含"没找到"空态）在输入变化时不清空，残留到下一次查询前，看起来就像"刚输入就说找不到"。
- **修复**：`Onboarding.tsx` 两个找回输入框改值即 `setLookupResults(null)`；Enter 触发加 `!e.nativeEvent.isComposing` 守卫（`CodeCustomizer.tsx` 的 Enter 同病同药）。
- **验证**：`npm run build` + `weapp npx tsc --noEmit` 通过；前端无测试基座，靠手测：中文输入法打字选词不再触发查询，改输入后"没找到"立即消失。
- **预防**：**所有中文输入框的 Enter 快捷提交一律带 `isComposing` 守卫**；异步查询结果必须与输入联动失效（输入即清），否则必现"灵异空态"。

## BUG-010 找回面板的复制按钮点了没反应

- **日期**：2026-08-14
- **环境**：本机 dev（http 访问场景必现，生产 http://IP:8000 同在）
- **现象**：按名字找回身份码的结果列表里，点「复制」按钮无任何反馈，剪贴板无内容。
- **根因**：`copyLookupCode` 直接调 `navigator.clipboard.writeText`——该 API **仅在安全上下文（HTTPS 或 localhost）可用**；用 IP+端口走 http 访问时 `navigator.clipboard` 为 undefined，异常被静默 catch，表现为"点了没反应"。项目里其实早有带 `execCommand` 兜底的 `copyText`（Onboarding 本地函数），新代码没复用。
- **修复**：`copyText` 提升到 `src/lib/utils.ts` 共享；`Onboarding.tsx` 找回复制改用它（失败提示"长按手动复制"），`App.tsx` 顶栏身份码复制同源修复。
- **验证**：`npm run build` + `weapp npx tsc --noEmit` 通过；手测：http://IP 访问下点复制正常入剪贴板。
- **预防**：**剪贴板写入一律走 `src/lib/utils.ts` 的 `copyText`**（clipboard API + execCommand 双通道），禁止直接调 `navigator.clipboard`；新功能复用工具函数前先全局搜一遍现有实现。


## BUG-011 Windows 本机 `npm run dev` 报 "'bash' 不是内部或外部命令"

- **日期**：2026-08-17
- **环境**：本机 dev（Windows，PowerShell）
- **现象**：Windows 上 PowerShell 里 `npm run dev` 直接失败：`'bash' 不是内部或外部命令，也不是可运行的程序或批处理文件`，前后端都起不来。
- **根因**（两条叠加）：
  1. `package.json` 的 `dev` 脚本是 `bash scripts/dev.sh`；npm 在 Windows 上用 cmd 执行脚本，而本机 Git Bash 的 `bash.exe`（`C:\Program Files\Git\bin\bash.exe`）不在系统 PATH 里 → 命令解析失败。
  2. 连带问题：`scripts/dev.sh` 的 venv 探测链只认 `.venv`（Windows 残留，本机不存在）和 `.venv-mac`（Mac 环境，Windows 上不可执行），不认识本机新建的 `server/.venv-win`——即使 bash 能跑也会报"找不到可用的 Python 虚拟环境"。
- **修复**：
  - `package.json`：`dev` 改为 `bash scripts/dev.sh || "C:/Program Files/Git/bin/bash.exe" scripts/dev.sh`——Mac/Linux 上 bash 在 PATH 直接成功走不到兜底；Windows cmd 上第一条失败后回退 Git Bash 全路径
  - `scripts/dev.sh`：venv 探测链头部加 `.venv-win/Scripts/python.exe` 分支（Windows 本机环境优先），注释同步更新
- **验证**：后台起 `npm run dev`，12 秒后 `curl http://127.0.0.1:8000/docs` 与 `http://127.0.0.1:7100/` 均 200；验证后已停掉任务避免占端口。
- **预防**：跨平台 npm script 里的 shell 命令要有 Windows 兜底路径；新建 venv 目录后同步检查 `scripts/dev.sh` 探测链、`.gitignore`、AGENTS.md 三处（本次已补齐）。

## BUG-012 移动端顶栏「我们」与「碎片墙」重叠（5 个 tab 撑爆 flex）

- **日期**：2026-08-17
- **环境**：本机 dev + 生产（375px 移动端必现）
- **现象**：个人功能上线后顶栏变为 5 个 tab（碎片墙/知识库/愿望清单/关系/我的），375px 宽度下「我们」标题与首个 tab 文字重叠，导航不可用。
- **根因**：顶栏右侧操作区是 `flex` 平铺（tab 导航 + 昵称 + 身份码/人格/通知/切圈/换身份 5 个文字按钮），没有任何 `min-w-0`/收缩约束；tab 数量从 4 加到 5 后总宽超过 375px，flex 溢出表现为文字互相覆盖（`App.tsx` 原 TABS 与 header 布局）。
- **修复**（随账号系统与导航重构一并落地）：
  - 顶栏 tab 收敛为 4 项：碎片/愿望清单/朋友任务/关系；「我的」「知识库」出顶栏（知识库并入 /search 图标入口，个人功能进汉堡「个人」页）
  - 圈名 `hidden sm:inline`，移动端不占位；nav 加 `min-w-0 flex-1 overflow-x-auto` + tab `whitespace-nowrap`，空间不足时导航内部横滑而不再外溢；标题与图标组 `shrink-0`
  - 桌面端平铺的 5 个文字按钮全部移除，汉堡下拉统一只留 设置/个人 两项（通知开启入口迁到 /settings）
- **验证**：`npm run build` 通过、`cd weapp && npx tsc --noEmit` 通过；375px 静态核算：内容区 351px，固定元素（标题+搜索+汉堡）约 100px，nav 弹性吸收余量，溢出只会触发 nav 内部滚动，结构上与文字不再可能重叠。
- **预防**：**顶栏/底栏新增入口前先做 375px 宽度核算**；横向排列的一组按钮必须有一个 `min-w-0 + overflow-x-auto` 的弹性吸收区，图标入口优先于文字入口。
