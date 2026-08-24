# 「我们」AI 技术实现详解（面试版）

> 覆盖简历上五个 AI 技术点的**代码位置、实现思路、选型理由（为什么用它）、数据支撑**。
> 所有引用带 `文件:行号`，数字均可溯源（优化实录 / BUG 记录 / 评测历史 jsonl）。
> 适用场景：面试被追问「你这个 RAG/Agent 具体怎么做的」时的应答底稿。

## 数据总览（一句话版）

| 技术点 | 核心数字 |
|---|---|
| LangGraph 图编排 | 倾诉轮串行 LLM 调用 5 次 → **2 次**；厂商超时整轮 500 → 降级续跑 |
| RAG 检索链路 | 双路召回 + RRF 融合 + 30 天半衰期衰减；评测 26/26（检索 ≥0.8、人设 1.0） |
| 护栏前置 | 自伤危机轮 LLM 调用 3 次（含 120s 生成后丢弃）→ **0 次** |
| 多模态识别提速 | 单次识别 14.9s → **1.8s**（≈8 倍）；以图搜图阈值 0.9（同图 ≈0.999 / 不同食物 ≈0.42） |
| 评测驱动优化 | 匹配评测 want **3/11 → 8/11**；小西红柿 196% 误差 → 0%；must 19/19 零回退 |

---

## 1. LangGraph 状态图编排（情绪树洞对话 Agent）

### 相关文件

| 文件 | 职责 |
|---|---|
| `server/app/services/treehole/graph.py` | 状态定义、五节点、条件边、checkpoint 装配 |
| `server/app/services/treehole/service.py` | `send_message` 主流程、任务层包裹、502 映射 |
| `server/app/services/treehole/streaming.py` | 流式回调的线程本地槽 |
| `server/app/api/treehole.py` | `/chat`（整包）与 `/chat/stream`（SSE）端点 |
| `server/app/services/tasks.py` | 任务层：`task_runs` 状态落库、降级标记消费 |

### 怎么实现的

**状态结构**（`graph.py:41-56`）：`TreeholeState(TypedDict, total=False)`，字段含
`message/intent/hits/profile/tool_results/citations/reply/guardrail`，以及两个会话级字段
`summary`（滚动摘要）与 `summary_upto`（摘要游标）——这两个靠 checkpoint 跨轮持久化，其余每轮重写。

**图装配**（`graph.py:192-214`）：主流水线是线性的，唯一分支在路由节点之后：

```
START → route_intent →(条件边: guardrail? writeback : retrieve)→ retrieve → tools → generate → writeback → END
```

- **route_intent**（`graph.py:61-79`）：先跑确定性护栏（见 §3）；未命中则**一次 LLM 调用同时产出
  intent 与检索 query**（原来是 route、rewrite 两次串行调用，合并是提速手段之一）。
- **retrieve**（`graph.py:82-89`）：`query or 原句` 走混合检索，同时拉 L3 记忆画像。
- **tools**（`graph.py:92-117`）：工具循环最多 3 轮；**vent（倾诉）轮在节点入口直接短路返回空**
  （`graph.py:93-95`）——倾诉不查账本不查计划，省掉一次「决定不做什么」的 tool-plan 空转。
- **generate**（`graph.py:120-148`）：组装人设卡、画像、L1 原子 top5、检索 hits、滚动摘要、
  工具结果、最近 20 条原文，调 `ai.treehole_reply` 生成。
- **writeback**（`graph.py:151-174`）：L0 原文落库（user+assistant 两条）→ 非护栏轮做 L1 原子
  记忆抽取 → 推进滚动摘要（最近 20 条之外每攒满 10 条增量压缩一次）。

**会话状态持久化**：`SqliteSaver` 落同一个 `app.db`（`graph.py:182-189`，独立连接 + WAL +
30s busy_timeout，与业务连接分离防事务干扰）；`thread_id = f"treehole:{account_id}"`
（`graph.py:217-218`），一账号一线程；清空历史时按 thread_id 删 checkpoint 行（L1/L2 记忆保留——
「清空的是对话，不是记忆」）。

**降级链**（设计哲学：「降级不 500，静默不降级」）：

| 节点 | 失败行为 | 依据 |
|---|---|---|
| 路由 LLM 挂 | 默认 vent + 原句检索（倾诉是最安全口径） | `graph.py:76-79` |
| 工具决策 LLM 挂 | 跳出循环，当轮无工具继续生成 | `graph.py:106-109` |
| 生成挂 | **不降级**，如实 502「树洞这会儿走神了」 | `service.py:93-96` |
| 压缩 / L1 抽取挂 | 保留旧摘要 / 跳过写回，记 degraded | `ai/__init__.py:751-754, 767-770` |

每个降级点调 `ai.mark_degraded()`（线程本地置位），任务层 `run_task` 据此把本轮落
`task_runs(status=degraded)`——降级从「无人知晓」变成「可查询可统计」。

### 为什么用 LangGraph

1. **状态机显性化**：多轮对话本质是带状态的分支流程（路由分流、护栏短路、工具循环），
   图的节点/条件边把这套控制流写成可读结构，而不是一坨 if-else 嵌套的 pipeline 函数。
2. **checkpoint 白拿**：会话级状态（滚动摘要 + 游标）随每次 invoke 自动快照进 SQLite，
   断点续聊、清空重开都不用自己设计持久化。
3. **节点可测**：每个节点是纯函数式的 state→partial-state 映射，配合确定性桩
   （`tests/fakes.py`）可以离线测「护栏轮生成节点零调用」这类精确断言，测试不触网。
4. **生态衔接**：记忆抽取用的 langmem 与 LangGraph 同生态，接入成本最低
   （调研过 mem0 等替代品后弃用，见 `交接文档-Agent化改造.md` §一）。

**数据支撑**：route+rewrite 合并 + vent 短路后，倾诉轮（最高频轮次）串行 LLM 调用
**5 次 → 2 次**；Kimi 单步超时从「整轮 500、用户白等最长 2 分钟」改为「降级续跑」。

---

## 2. RAG 检索链路

### 相关文件

| 文件 | 职责 |
|---|---|
| `server/app/services/treehole/retrieve.py` | 混合检索、RRF 融合、时效衰减、rerank 接口位 |
| `server/app/services/treehole/graph.py:82-89` | 检索节点接线（query 来源、画像拉取） |
| `server/app/ai/prompts.py:388-399` | 路由 prompt（intent + query 一次产出） |
| `server/app/db/database.py:788-807` | embedding BLOB 编解码 + numpy 余弦 |

### 怎么实现的

完整链路：**查询改写 → 双路召回 → RRF 融合 → 时效衰减 → 注入 prompt + 引用落地**。

1. **查询改写**：不做独立改写调用，而是与意图路由合并为一次 JSON 调用产出
   `{"intent", "query"}`；vent 轮不改写，用原句做「共鸣检索」（找「你之前也说过…」的素材）。
2. **双路召回**（`retrieve.py:75-115`），检索对象是本人碎片 + L1 原子记忆两张表：
   - **向量路**：numpy 暴力余弦全表扫 `embedding IS NOT NULL` 的行，各取 top `limit*2`；
   - **关键词路**：确定性 bigram 抽取（去停用字、去重、最多 3 个），
     每个 bigram 走 `LIKE %kw%`（`%`/`_` 先转义）取最新 10 条。
3. **RRF 融合**（`retrieve.py:63-69`）：标准倒数名次融合 `score += 1/(60+rank)`，
   同一条目在向量路和多个关键词路命中会累加多份——双路命中自然排前。
4. **时效衰减**（`retrieve.py:31-37`）：`max(0.3, 0.5^(days/30))`——半衰期 30 天，
   下限 0.3 防止老记忆永不翻身。
5. **注入与引用**：hits 进 system prompt 的【检索到的碎片】块；前 3 条同时落 `citations`
   （`graph.py:122-125`），assistant 回复显式引用来源（"你上周三说过…"），
   citations 随 L0 持久化，前端可回溯原句。

**rerank 接口位**（`retrieve.py:49-52`）：当前是恒等函数（RRF 序即最终序），契约是
`query + 候选 → 重排`，将来要接 LLM 精排只改这一个函数。

### 为什么这么设计

- **为什么双路而不是纯向量**：纯向量对「专有名词精确匹配」不敏感（用户问「冰岛」，
  语义相近的「北欧」可能排过含原词的碎片），关键词路补足精确命中；纯关键词则抓不住语义相似。
  RRF 是零参数的融合方法，不需要标注数据训权重，小数据规模下最务实。
- **为什么不上向量数据库 / 不用 FTS5 BM25**：向量 DB 解决的是百万级 ANN 规模问题，
  不影响检索质量——几千条碎片暴力余弦是毫秒级且零基础设施；FTS5 对中文整词分词失效，
  LIKE bigram 反而更可控。检索层保留接口抽象，十万级再切 pgvector 不改业务
  （完整论述见 README「选型取舍」）。
- **为什么要时效衰减**：树洞是陪伴场景，「最近的情绪」比「三个月前的一句话」更该被看见；
  但设 0.3 下限，是因为老记忆（如半年前的目标）被问及时仍要能召回。

**数据支撑**：树洞确定性评测集 26/26 达标，其中检索命中率 ≥0.8、事实保留率 1.0
（阈值钉在 `scripts/treehole_eval.py`，改检索后必跑回归）。

---

## 3. tool calling 与确定性护栏前置

### 相关文件

| 文件 | 职责 |
|---|---|
| `server/app/services/treehole/tools.py` | 自研工具注册表 + 5 个业务工具 |
| `server/app/services/treehole/guardrail.py` | 确定性自伤护栏词表 |
| `server/app/services/treehole/graph.py:68-71, 92-117` | 护栏前置 + 工具循环 |
| `server/app/ai/prompts.py:402-413` | tool-plan prompt（JSON 输出 calls） |
| `server/app/ai/llm.py:168-212` | Kimi `$web_search` 回声协议工具循环 |

### tool calling 怎么实现的

**自研 JSON-plan 循环，而不是 OpenAI tools 协议**：

1. 注册表是字典（`tools.py:151-158`），5 个工具统一签名
   `(account_id, args) -> {"name","summary","data"}`：`query_ledger`（本月支出）、
   `query_today_plan`（今日计划完成度）、`query_calories(day?)`（某日热量+菜名明细）、
   `search_fragments(keyword)`（搜碎片）、`get_memory_profile()`（读记忆画像）。
   全部只读、账号级隔离。
2. 每轮工具节点先做一次 `chat_json`：把工具说明书（`specs_text()` 拼注册表 desc）+
   用户消息 + **已拿到的结果**回填进 prompt，模型输出 `{"calls": [...]}` 或 `calls: []` 收工，
   循环上限 3 轮（`graph.py:101-117`）。
3. 工具结果的 `summary`（一句人话）进下一轮 plan；最终整包 JSON 以 1500 字符预算截断后进
   生成 prompt 的【数据查询结果】槽，prompt 规则要求「直接采信，不要编造数字」。
4. 容错：未注册的工具名返回空 summary（防模型幻觉工具名搞崩图）；工具决策 LLM 挂了
   就当轮无工具继续生成（降级，不 500）。

**为什么自研而不走 `tools=[...]` function-calling 协议**：
- 厂商兼容：项目要跨多家 OpenAI 兼容厂商（阿里百炼 / Kimi k3 走 kimi.com/coding 网关），
  tools 协议在网关上踩过坑（BUG-017：Kimi 回显 `builtin_function` type 被网关 400）；
  JSON-plan 只需要最基础的 chat 能力，哪里都能跑。
- 可控可测：plan 是显式 JSON，断言「这轮该调 query_calories」就是比对 dict，
  确定性桩下零成本回归。
- 联网搜索是**另一套独立机制**：Kimi 内置 `$web_search` 走官方回声协议
  （assistant 的 tool_calls 原样回传 role=tool，Kimi 服务端执行搜索，≤3 轮），
  与数据工具的 JSON-plan 互不干扰——两套机制各取所需。

### 护栏前置怎么实现的

护栏本身是**纯确定性规则**（`guardrail.py`）：16 个强烈自伤信号词 + 9 个口语夸张缓冲
（「困得/尴尬得/无聊得想死」先命中信号再被缓冲语境豁免）。

前置的实现在路由节点开头（`graph.py:68-71`）：

```python
if guardrail.is_strong_self_harm(state["message"]):
    return {"intent": "vent", "guardrail": True, "reply": guardrail.INTERVENTION_TEXT, ...}
# 条件边看 guardrail 标记直达 writeback，检索/工具/生成整段跳过
```

配套细节：护栏轮**跳过 L1 记忆抽取**（`graph.py:163`）——危机倾诉是求助信号，
不沉淀为记忆条目；流式端点下护栏轮没有 delta，done 毫秒级到达。

### 为什么这么做

- **为什么用确定性词表而不是模型判断**：护栏是可解释、可回归的红线，不能随模型版本、
  采样温度波动；词表每一次扩充（如补「不想存在/想解脱」这类否定式存在表达）都有测试钉住，
  普通情绪宣泄（"今天累死了"）零误伤也是测试断言。
- **为什么前置到路由节点**：改造前护栏在生成**之后**检查——命中时 120s 的生成已经花掉
  再整条替换。但检查对象本来就是**用户消息**，没有任何理由等生成完。前置后命中即产出
  干预话术、条件边直达写回。

**数据支撑**：自伤危机轮 LLM 调用 **3 次（含 120s 生成后丢弃）→ 0 次**；
评测集中护栏触发正确率 1.0（强自伤命中 / 普通情绪不误伤）。

---

## 4. 多模态识别提速（拍照热量识别）

### 相关文件

| 文件 | 职责 |
|---|---|
| `server/app/services/ledger.py:328-427` | `recognize_calorie` 识别主流程 |
| `server/app/services/ledger.py:513-575` | `_image_rag_recall` / `_food_image_store`（以图搜图） |
| `server/app/ai/__init__.py:418-465` | `recognize_food`：校准装配 + 两级策略 |
| `server/app/ai/vision.py:18-37` | 视觉调用（temperature=0、400 剥参重试） |
| `server/app/ai/embedding.py:33-51` | 多模态 embedding（doubao-embedding-vision） |
| `server/app/api/ledger.py:80-88` | 识别入口（联网回填挂 BackgroundTasks 移出热路径） |

### 怎么实现的（四个提速手段叠加）

**① 两级识别策略**（`ai/__init__.py:447-460`）：默认思考档 off 快出（实测 **1.8s**）；
只有模型自报 `packaged=true` 但 `brand` 为空（= 包装食品却没读出品牌，快档漏了包装小字）
才带思考重试一次（14.9s），重试结果**整体替换**（不逐 item 合并，不混口径）。
——思路是「按需付延迟」：读包装小字这种精细活才花思考的钱，常规餐不付。

**② 识别副本降载**：前端 canvas 零依赖产出三份图（原图 + 1600px 展示图 + 800px 识别副本，
`src/lib/image.ts:10-29`）；服务端识别优先用 `_s.jpg`（`ledger.py:41-54`），
展示仍用 1600px。传输与 base64 编码时间省掉，分辨率对认菜名/读品牌足够。

**③ 以图搜图（图片型 RAG）**：
- 向量来源：多模态 embedding（doubao-embedding-vision），与文本向量同空间；
- 写入：确认入账时，单菜品+有图的记录把 `name/brand/kcal_per_100g` 与图片向量存进
  `calorie_food_images`（账号级隔离）；
- 召回（`ledger.py:513-545`）：模型识别结果恰好 1 个菜品时，全表扫余弦取 top1，
  **≥0.9 才命中**，命中后复用菜名/品牌/单价（`source="image_rag"`）——
  克数仍用本次估计（同一碗饭的分量每次不同，但「同物同价」）。

**④ 联网移出热路径**：未命中菜名先按模型估值返回（钳制 ≤1000 kcal/100g），
联网查询挂 BackgroundTasks 后台回填（线程池 3 并发 + 任务层重试 2 次），
入库后经 SSE 推 `staging_ready` 前端自动刷新——首屏不等 20-25s 的联网调用。

另外统一 `temperature=0`（`vision.py:25`）：消除采样抖动，同图同结果，可回归可评测。

### 为什么这么做

- 阈值 0.9 的依据是**实测分布**（注释在 `ledger.py:26`）：同图 ≈0.999、不同食物 ≈0.42、
  食物 vs 人像 ≈0.23——分离度极大，0.9 几乎没有误判空间；命中即「识别抖动归零」，
  因为结果直接复用上次的确认值。
- 「识别与计算分离」是整条管线的总思路：模型只负责认菜名+估克数（它擅长），
  热量一律查《中国食物成分表》计算（它不擅长算术），模型估值只是兜底且被物理上限钳制。

**数据支撑**：常规餐单次识别 **14.9s → 1.8s（约 8 倍提速）**；提速同时准确率不降反升
（见 §5 的评测数字）。

---

## 5. 评测驱动的识别准确率优化

### 相关文件

| 文件 | 职责 |
|---|---|
| `server/scripts/eval_food.py` | 30 用例匹配评测（must/want 分级、退出码、jsonl 趋势） |
| `server/scripts/eval/food_match_history.jsonl` | 评测历史（改前/改后各一条，可 `--compare`） |
| `server/app/services/nutrition.py` | `match()` 五级降级链、别名激活、双向单字护栏 |
| `server/app/ai/prompts.py:294-324` | FOOD_PROMPT：「容器×密度」两步推理 + confidence |
| `server/app/services/ledger.py:598-618` | `_gram_bias` 用户级克数偏置 |

### 怎么实现的

**第 0 步先建评测**（这是方法论核心：没有数字就没有「改好了」的判据，也无法防回退）：
- 30 个种子用例，参照值全部钉在本地库《中国食物成分表》**真实行**上（米饭→粳米饭(蒸)118）；
- **must 级 19 例**（基本盘，通过率 <95% 退出码非 0，可挂 CI）+
  **want 级 11 例**（别名/向量/组合菜的改进目标位，只报告不卡退出）；
- 每次跑追加一行 JSON 进 `food_match_history.jsonl`，`--compare` 看前后对比——
  「改 prompt/匹配必跑评测」成为纪律。

**准确率三件套**：

1. **别名激活 + 双向单字护栏**（`nutrition.py:57-69, 116-124`）：成分表行名的方括号别名
   （`马铃薯[土豆、洋芋]`，含厂商破损括号）自动提取、与主名同级参与匹配——**零手工别名表**，
   1350 行数据的别名全部激活。单字护栏双向化：查询侧防「茶→茶肠」，
   候选侧防「小西红柿→柿」（单字键不参与互含扩展）。
2. **克数两步推理 prompt**（FOOD_PROMPT 规则 5）：把「裸猜克数」改成乘法——
   先认容器估体积（小饭碗 200ml / 家常碗 300ml…），再乘品类密度
   （熟饭面 0.6 / 液体 1.0 / 叶菜 0.3 g/ml）。克数是热量误差的最大头，视觉模型裸猜是弱项，
   给它参照系和公式比让它「好好估」有效得多。
3. **用户级偏置 `_gram_bias`**（`ledger.py:598-618`）：取该用户最近 30 条克数纠正的
   「实际/估计」**中位比值**；样本 <3 不给、中位偏离 ±50% 判数据不可信不注入、
   与 1 差 <5% 视噪声不注入；通过则注入一行
   「你历来的分量估计比该用户实际平均偏低约 25%，本次所有克数按 ×1.25 修正」。
   逐条样例只帮同类食物，系统偏置帮所有食物；用中位数不用均值，抗离群。

配套还有 `confidence` 自报字段（0-1，<0.5 前端标「没把握」）——把稀缺的人工确认
引导到最需要的地方，纠正数据越多，校准飞轮越快。

### 为什么这么做

- **为什么别名不建同义词字典**：别名数据本来就在成分表的方括号里，只是被 normalize
  剥掉了；自动提取 = 零维护成本，且永远与数据同源，不会出现字典与库不一致。
- **为什么护栏要「双向」**：只防查询侧（BUG-019 的修复）不够——评测基线跑出了
  「小西红柿被单字行『柿』吸走」的候选侧案例（74 vs 25 kcal，**196% 误差实测**）。
  这正说明评测先行的价值：基线跑出的意外发现比数字本身更值钱。
- **为什么有意保留 3 个 miss**：炒鸡蛋/番茄炒蛋/红烧肉走「高油做法守卫」——
  炒鸡蛋绝不按煮鸡蛋计价，宁 miss 走联网拿做法级单价。评测体系要能把「有意行为」
  与「缺陷」区分开，否则优化会朝错误方向收敛。

**数据支撑**（`scripts/eval/food_match_history.jsonl` 改前/改后两条记录）：

| 指标 | 基线 | 改后 |
|---|---|---|
| must 通过率 | 19/19 | 19/19（提速+提准均无回退） |
| **want 通过率** | **3/11** | **8/11** |
| 土豆/洋芋/红薯/小西红柿 | miss 或 **196% 误差** | **全部 0% 误差命中** |

---

## 附：与旧文档口径的出入（面试如实口径）

写本文档时以代码为准核实过，以下两处旧文档（README / 交接文档）是**规划口径，实现已偏离**，
面试被问到时按实际回答：

1. **`services/treehole/compress.py` 不存在**：滚动摘要实际合并在
   `graph.py::node_writeback`（触发与游标）+ `ai.treehole_compress`（LLM 调用）里，
   触发条件「最近 20 条之外攒满 10 条」（`graph.py:37-38, 167-173`）。
2. **没有自研 SQLite BaseStore 适配层**：langmem 的 `create_memory_manager` 未传 store
   （默认内存 store 即用即弃），langmem 实际只承担「结构化抽取器」角色
   （四个 Pydantic schema 映射 preference/fact/event/commitment 四类原子）；
   持久化是手撸 SQL 落 `memory_atoms` 表，去重逻辑（精确文本相同或余弦 ≥0.9）在
   `layers.insert_atoms`（`services/memory/layers.py:85-145`）。取舍是放弃 langmem 的
   store 语义（跨轮 update/delete），换零适配成本与现有事务/隐私隔离的一致。
3. 一个已知小瑕疵：`node_route` 写入的 `query` 键未在 `TreeholeState` 声明
   （schema 里是 `rewritten_query`），langgraph 1.2.11 下可用但属隐式契约。

## 附：可观测性与质量闭环（贯穿全部技术点）

- 每轮树洞对话落 `task_runs`（status / 延迟 / degraded），降级可查可统计；
- 联网回填、L2 刷新等后台任务同样走任务层（指数退避重试、失败半截写入回滚保幂等）；
- 双评测脚本：`treehole_eval.py`（26 用例四指标）+ `eval_food.py`（30 用例 must/want），
  改 prompt / 检索 / 匹配后必跑回归；
- 24 条故障全部按「现象/根因/修复/验证/预防」登记 `docs/BUG记录.md`——
  包括「langmem 温度写死被厂商全量 400、L1 自上线零写入」（BUG-024）这类
  「对话正常但记忆从没写过」的隐蔽故障，修复后温度解析收敛为单点函数
  `llm.resolve_temperature()` 全出口共用。
