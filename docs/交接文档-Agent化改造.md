# 交接文档 - Agent 化改造（树洞 + 方案评审团）

> 2026-08-18 grilling 会话共识。目标：把项目升级为 AI 应用开发岗简历项目，显性体现 **RAG / tool calling / agent 框架 / 多 agent 协作**。原则：简历优先，但每个技术点必须有站得住的功能落点，不为塞而塞。

## 一、技术栈变化

| 变化 | 内容 |
|---|---|
| 新增依赖（白名单例外） | `langgraph`（+ `langchain-core`）。AGENTS.md 的「零新依赖」改为「白名单制」，LangGraph 是唯一例外 |
| 自研 | tool 注册表与工具集（树洞查数据用）；记忆层扩展 |
| 暂缓（backlog） | MCP：后续把应用数据能力（搜碎片/读画像/查账/查计划）封装成对外 MCP server（streamable HTTP + token），供 Claude Desktop 等客户端接入。本期不做 |
| 不变 | LLM/EMBEDDING/VISION 三组通用参数、FastAPI、SQLite、React+Vite 前端 |

简历关键词映射：**RAG** = 树洞检索链路（向量召回碎片 + 记忆画像 + 风格画像注入）；**tool calling** = 树洞工具节点；**agent 框架** = LangGraph 图编排；**多 agent 协作** = 方案评审团（fan-out/fan-in）。

## 二、情绪树洞（私密对话 agent）

**定位**：给用户倾诉的陪伴型 agent——解答疑惑、了解喜好、扮演用户想要的人格、提供情绪价值。私密：只服务账号本人，数据不出自己的屏幕（隐私铁律）。

**入口**：碎片墙页顶部入口卡 + 独立路由 `/treehole`（圈内壳；Self 侧 /me 亦可链接）。对话式多轮，历史持久化，可清空。

**LangGraph 状态图（五节点）**：

```
用户消息
  → ① 意图路由：倾诉 / 提问 / 需要查数据（LLM 判断，决定走不走③）
  → ② 检索（RAG 的 R+A）：
       - 向量召回相关历史碎片（复用 fragment embedding + 余弦）
       - user_profiles 记忆画像（复用现有蒸馏层）
       - 风格画像（新增：从用户碎片蒸馏语气/口头禅/表达节奏，见§四）
  → ③ 工具节点（tool calling，模型决定调不调）：
       query_ledger（本月支出/分类） / query_today_plan（今日计划完成度）
       / query_calories（今日热量） / search_fragments（关键词搜碎片）
       / get_memory_profile（读记忆画像）
  → ④ 人格化生成：树洞人格（用户自定义 > 预设，复用 resolve_persona 体系）
       + 风格画像 few-shot，共情回应；提问类走解答口径
  → ⑤ 记忆写回：从本轮对话抽取偏好/事实，实时写入记忆层
       （现有记忆层只有夜间批量蒸馏，这是新增的实时写入通道）
```

**会话状态**：LangGraph SqliteSaver checkpoint，落到同一个 `app.db`（独立表，不动现有表）。

**后端新增**：`api/treehole.py`（POST /api/treehole/chat 流式或整包响应、GET 历史、DELETE 清空）、`services/treehole/`（graph 定义、tools.py 工具集、style_profile.py）；表：`treehole_persona`（账号级人格设置）、风格画像字段挂 `user_profiles` 或独立表。

**前端新增**：`pages/TreeHole.tsx`（聊天 UI：消息流 + 输入框 + 人格设置入口），Wall 页加入口卡。复用 Markdown 组件渲染回复。

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

## 四、风格画像（RAG 的新增亮点）

从用户自己的碎片里蒸馏语言风格（语气词、句长、口头禅、emoji 习惯），生成时注入让树洞"像自己人说话"。复用现有夜间蒸馏管线加一个分量；周报语录 few-shot（`reports.py`）是其前身，两处共用同一套画像。

## 五、实施顺序与验证

1. 依赖白名单：requirements.txt 加 langgraph，AGENTS.md 改口，README/技术栈文档更新
2. 树洞后端：schema → tools → graph → API；测试覆盖（意图路由 mock、工具调用、记忆写回、隐私边界=只能读自己的数据）
3. 树洞前端：TreeHole 页 + Wall 入口卡
4. 评审团：plans.py 图编排 + 响应扩展 + 前端展示；测试覆盖（并行评审聚合、降级=评审失败仍出终稿）
5. 收尾：pytest 全绿 + `npm run build` + `cd weapp && npx tsc --noEmit`（core 只兼容扩展）；README 加 agent 架构图说明

**测试约束不变**：强制 mock 模式，LangGraph 图在 mock LLM 下也要能跑通（节点逻辑可测）。

## 六、明确不做

- 不上 MCP（backlog）
- 树洞不做圈内可见、不做多人格轮流发言（破坏"被倾听"体验）
- 不引入 LangChain 全家桶其他件、不用 LangSmith 之外的额外 SaaS（LangSmith 可选不配）
- 不动 weapp/；不动现有碎片管线结构（风格画像只是加分量）
