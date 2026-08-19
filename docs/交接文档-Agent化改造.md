# 交接文档 - Agent 化改造（树洞 + 方案评审团）

> 2026-08-18/19 两轮 grilling 会话共识。目标：把项目升级为 AI 应用开发岗简历项目，显性体现 **RAG / tool calling / agent 框架 / 多 agent 协作 / 记忆系统 / 评估闭环**。原则：简历优先，但每个技术点必须有站得住的功能落点，不为塞而塞。

## 一、技术栈变化

| 变化 | 内容 |
|---|---|
| 新增依赖（白名单例外） | `langgraph`（+ `langchain-core`）、`langmem`（记忆抽取/巩固，LangGraph 同生态官方库）。AGENTS.md 的「零新依赖」改为「白名单制」，仅这两个例外 |
| 自研 | tool 注册表与工具集（树洞查数据用）；SQLite 版 LangGraph BaseStore 适配层（langmem 官方只有内存/Postgres store）；L0-L3 分层记忆；混合检索与 rerank；评估脚本 |
| 暂缓（backlog） | MCP：后续把应用数据能力（搜碎片/读画像/查账/查计划）封装成对外 MCP server（streamable HTTP + token），供 Claude Desktop 等客户端接入。本期不做 |
| 调研后弃用 | mem0（要拖向量库+推翻现有记忆层）、腾讯 WeKnora（完整知识库平台，非嵌入库）、TencentDB-Agent-Memory（面向 coding agent 团队的独立三服务，只抄其 L0-L3 分层思想）、向量数据库（见§六） |
| 不变 | LLM/EMBEDDING/VISION 三组通用参数、FastAPI、SQLite、React+Vite 前端 |

简历关键词映射：**RAG** = 树洞检索链路（查询改写 → 混合检索 BM25+向量 RRF 融合 → rerank → 画像注入）；**tool calling** = 树洞工具节点；**agent 框架** = LangGraph 图编排；**多 agent 协作** = 方案评审团（fan-out/fan-in）；**记忆系统** = langmem + L0-L3 分层记忆（借鉴 TencentDB-Agent-Memory）；**评估闭环** = 树洞评估集 + 回归脚本。

## 二、情绪树洞（私密对话 agent）

**定位**：给用户倾诉的陪伴型 agent——解答疑惑、了解喜好、扮演用户想要的人格、提供情绪价值。私密：只服务账号本人，数据不出自己的屏幕（隐私铁律）。

**入口**：碎片墙页顶部入口卡 + 独立路由 `/treehole`（圈内壳；Self 侧 /me 亦可链接）。对话式多轮，历史持久化，可清空。

**LangGraph 状态图（六节点）**：

```
用户消息
  → ① 意图路由：倾诉 / 提问 / 需要查数据（LLM 判断，决定走不走③）
  → ② 检索（RAG 的 R+A）：
       - 查询改写：把情绪化输入改写成检索友好 query（"烦死了又是那种事"→"用户近期工作压力相关碎片"）
       - 混合检索：BM25 关键词（SQLite FTS5）+ 向量召回，RRF 融合排序，
         再 LLM rerank 取 top5；打分叠加 时效衰减×重要性
       - user_profiles 记忆画像 + 偏好画像（见§四）
  → ③ 工具节点（tool calling，模型决定调不调）：
       query_ledger（本月支出/分类） / query_today_plan（今日计划完成度）
       / query_calories（今日热量） / search_fragments（关键词搜碎片）
       / get_memory_profile（读记忆画像）
  → ④ 人设扮演生成：**酒馆式人设卡**（用户设立一次、持久记住、每轮扮演，见§四）
       + 偏好画像注入（按用户喜好组织回答）+ 引用落地（回答显式引用碎片/记忆来源，
       "你上周三说过…"，带来源 id 可回溯），共情/解答按意图走不同口径
  → ⑤ 安全护栏：生成后检。仅命中强烈自伤意愿时替换为干预话术（求助渠道提示）；
       普通情绪波动不打扰
  → ⑥ 记忆写回：记住用户说了什么、发了什么——从本轮对话抽取偏好/事实/事件，
       实时写入 L1 原子记忆（langmem，见§四）
```

**会话状态与压缩**：LangGraph checkpoint 落同一个 `app.db`（独立表，不动现有表）。上下文压缩策略：人设卡/偏好画像/最近 10 轮原文**永不压缩**；更早历史走**滚动增量摘要**（填槽式模板：关键事实/情绪轨迹/待跟进/时间锚点，条目带源消息 id 可回溯，不重压不漂移）；检索结果只塞命中片段。压缩质量由评估集的事实保留率指标验收（§五）。

**后端新增**：`api/treehole.py`（POST /api/treehole/chat 流式或整包响应、GET 历史、DELETE 清空、PUT /api/treehole/persona 设立人设卡）、`services/treehole/`（graph 定义含护栏节点、tools.py 工具集、retrieve.py 混合检索+RRF+rerank、compress.py 滚动摘要）；`services/memory/`（langmem 集成 + SQLite BaseStore 适配 + L0-L3 分层，见§四）；表：`treehole_persona`（账号级人设卡）、`memory_atoms`（L1）、`memory_scenarios`（L2）、`treehole_messages`（L0，与 checkpoint 表并存）——新表独立，不动现有表。

**前端新增**：`pages/TreeHole.tsx`（聊天 UI：消息流 + 输入框 + 人设卡设立入口），Wall 页加入口卡。复用 Markdown 组件渲染回复。

## 三、方案评审团（多 agent 协作）

**改动点**：`services/plans.py` 的方案生成从「LLM 抽目的地 → 高德数据 → LLM 成稿」扩展为 LangGraph 编排：

```
生成触发 → 起草人格出草案
        → 并行评审（fan-out）：从 PLAN_KINDS 六类人格挑 2~3 个（务实挑刺/浪漫加码/毒舌吐槽）
        → 汇总人格修订出终稿（fan-in）
```

- 响应结构扩展：`{draft, reviews:[{persona, comment}], final}`，旧字段保留兼容
- 前端方案页展示评审过程（各人格评审卡 + 终稿），这是多 agent 最直观的可见证据
- 成本：每次方案多 2~3 次 LLM 调用；方案有池子指纹缓存兜底，频率可控

## 四、人设卡与分层记忆（树洞的两大个性化支柱）

**人设卡（酒馆式，SillyTavern-like）**：用户给树洞 AI 设立人设——名称、性格、说话风格、与用户的关系、背景设定，设立一次持久保存，之后每轮对话都按卡扮演。前端提供设立/修改入口（树洞页设置区）；未设立时用默认倾听者人设。与圈人格体系（resolve_persona）相互独立：圈人格管周报，人设卡管树洞。

**L0-L3 分层记忆**（借鉴 [TencentDB-Agent-Memory](https://github.com/TencentCloud/tencentdb-agent-memory) 的分层模型，自研实现；其 PersonaMem benchmark 48%→76% 验证了分层对"懂用户"的收益）：

| 层 | 存什么 | 写入方式 |
|---|---|---|
| L0 对话原文 | 树洞对话全文 | 每轮落库，永不删（摘要不替代原文，可回溯） |
| L1 原子记忆 | 一条一事实：喜好/雷点/习惯/事件/承诺（"在攒钱想去冰岛"） | 每轮对话后 langmem 实时抽取（hot path） |
| L2 场景记忆 | 按主题组织的工作上下文块（围绕某目标/某段关系的多条 L1 聚合） | 后台异步（langmem background manager） |
| L3 长期画像 | 稳定偏好与性格模式 | 复用现有夜间蒸馏管线扩展 |

检索时 L3/L2 常驻注入做底，按查询动态召回 L1，需要原文佐证时下钻 L0。生成注入按条数+字数预算截断。偏好画像即 L3 的用户向切面——不是学用户说话，而是搞懂用户。

**弃用 mem0 的原因**：需要向量库、默认绑 OpenAI 生态、且会替换而非增强现有蒸馏体系；langmem 与 LangGraph 同生态、可插拔到我们自己的 SQLite store，增量最大成本最小。

## 五、评估闭环与实施顺序

**评估集**（质量的唯一硬指标，先于优化落地）：20~30 条树洞问答评估集，覆盖——事实保留率（历史埋点事实压缩后召回检查）、检索命中率、人设一致率、护栏触发正确率（强自伤命中/普通情绪不误伤）。LLM 当裁判做 pairwise 对比（全文 vs 压缩、改 prompt 前后）。纯自研脚本零新依赖，挂在 `server/scripts/`，每次改 prompt/检索后跑回归。

**实施顺序**：

1. 依赖白名单：requirements.txt 加 langgraph+langmem，AGENTS.md 改口，README/技术栈文档更新
2. 记忆底座：SQLite BaseStore 适配 → langmem 接入 → L0-L3 表与写入链路
3. 树洞后端：tools → 混合检索（FTS5+向量 RRF+rerank）→ graph（含护栏节点）→ API；测试覆盖（意图路由 mock、工具调用、记忆写回、护栏触发/不误伤、隐私边界=只能读自己的数据）
4. 评估集：造数据 + 回归脚本，验收检索与压缩质量
5. 树洞前端：TreeHole 页 + Wall 入口卡
6. 评审团：plans.py 图编排 + 响应扩展 + 前端展示；测试覆盖（并行评审聚合、降级=评审失败仍出终稿）
7. 收尾：pytest 全绿 + `npm run build` + `npx tsc -b`；README 加 agent 架构图与选型说明（含向量数据库的规模化取舍论述）

**测试约束不变**：强制 mock 模式，LangGraph 图在 mock LLM 下也要能跑通（节点逻辑可测）。

## 六、明确不做

- 不上 MCP（backlog）
- 树洞不做圈内可见、不做多人格轮流发言（破坏"被倾听"体验）
- 不引入 LangChain 全家桶其他件、不用 LangSmith 之外的额外 SaaS（LangSmith 可选不配）
- **不上向量数据库**：向量 DB 解决的是百万级 ANN 规模问题，不影响检索质量；几千条碎片暴力余弦是毫秒级且零基础设施。检索层保留接口抽象，十万级再切 pgvector——这个取舍论述本身写进 README
- 不接入 mem0 / WeKnora / TencentDB-Agent-Memory（调研结论见§一，TencentDB 只借鉴 L0-L3 分层思想）
- 不动现有碎片管线结构；树洞不学用户说话风格（要的是懂喜好+扮演人设，不是模仿）
