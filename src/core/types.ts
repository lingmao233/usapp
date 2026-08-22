/** 平台无关的数据类型定义（web 端）。 */

export interface Session {
  circle_id: string
  user_id: string
  nickname: string
  circle_name: string
  invite_code: string
  /** 账号级字段（账号系统重构后随 session 写入） */
  account_id?: string
  username?: string
  has_password?: boolean
}

/** 登录态账号信息：auth.register/login/reset 的响应结构；
 * recovery_code 仅注册/找回时附带，前端必须强制展示一次 */
export interface AccountInfo {
  account_id: string
  username: string
  nickname: string
  has_password: boolean
  recovery_code?: string | null
}

/** 圈子（人格系统）：persona_custom 非空时优先于 persona_preset，圈与圈独立 */
export interface Circle {
  id: string
  name: string
  invite_code: string
  created_at: string
  persona_preset: string
  persona_custom: string
}

export interface Fragment {
  id: string
  user_id: string
  user_nickname: string
  circle_id: string
  content: string
  type: string
  tags: string[]
  mood: string
  created_at: string
  is_knowledge: boolean
  is_wish: boolean
  wish_category: string
  ai_summary: string
  processed: boolean
  visibility: "public" | "private"
  /** 互动计数（第 4 期）：仅公共碎片可被评论/点赞 */
  like_count: number
  comment_count: number
  liked_by_me: boolean
  /** 配图（发图片）：本站 /api/uploads/ 地址，无图为 null */
  image_url: string | null
}

/** 评论（第 4 期）：平铺返回，parent_id 为空为顶级评论，前端按 parent_id 组楼中楼 */
export interface Comment {
  id: string
  circle_id: string
  fragment_id: string
  author_id: string
  author_nickname: string
  parent_id: string | null
  content: string
  created_at: string
}

export interface RelatedFragment extends Fragment {
  similarity: number
}

export interface KnowledgeItem {
  id: string
  fragment_id: string
  title: string
  url: string
  content: string
  summary: string
  tags: string[]
  created_at: string
  user_nickname: string
  similarity?: number
}

export interface Wish {
  id: string
  user_id: string
  user_nickname: string
  content: string
  category: string
  status: string
  created_at: string
  plan: WishPlan | null
  visibility: "public" | "private"
  /** 配图（发图片）：本站 /api/uploads/ 地址，无图为 null */
  image_url: string | null
}

export interface WishPlan {
  time: string
  location: string
  budget: string
  steps: string[]
  /** 查实时价/地图跳转链接（高德/携程搜索） */
  links?: { label: string; url: string }[]
  /** 数据说明：哪些是真实查询、哪些是预估 */
  disclaimer?: string
  /** 参与人（生成时随方案一起落库，缓存命中也能拿到） */
  participants?: string[]
}

/** 方案追问消息（kind='plan' 线程） */
export interface ChatMessage {
  id: string
  role: "user" | "assistant"
  content: string
  created_at: string
}

export interface CommonWish {
  content: string
  matched_users: string[]
  suggestion: string
  confidence: number
  wish_ids: string[]
}

export interface ReportMeta {
  id: string
  week_start: string
  week_end: string
  created_at: string
}

export interface Report extends ReportMeta {
  content: string
  key_connections: string[]
}

/** 身份在某个圈子里的成员信息（"我的圈子"列表项） */
export interface AccountCircle {
  circle_id: string
  user_id: string
  circle_name: string
  invite_code: string
  my_nickname: string
  member_count: number
  fragment_count: number
  last_active: string | null
  joined_at: string
}

export interface AccountCirclesResp {
  account_id: string
  account_nickname: string
  circles: AccountCircle[]
}

/** 关系图（第 3 期）：观看者视角，服务端已按身份过滤（设计文档 §5/§6） */
export interface GraphNode {
  id: string
  nickname: string
  avatar: string
  created_at: string
}

export interface GraphEdge {
  user_a: string
  user_b: string
  /** 0-1 亲密度总分：只用于映射线宽/距离分档，界面不展示精确值 */
  score: number
  topics: { tag: string; source: string }[]
  /** 存在一个共同的秘密愿望（仅当事人双方可见，只提示存在不揭晓主题） */
  has_secret: boolean
  summary: string
}

export interface PairGraph {
  circle_id: string
  nodes: GraphNode[]
  edges: GraphEdge[]
}

/* ---------------- 个人功能（账号级：目标/计划/记账/热量/鞭策） ---------------- */

/** 目标：账号级一份，与圈子正交，挂在 account_id 上；共享走 self_sharing（类别 × 圈子开关），
 * viewer 过滤在服务端。params（目标参数）/answers（问卷答案）/framework（规则算出的周期框架）
 * 字段随类型而变，统一按 Record 读取。 */
export interface Goal {
  id: string
  account_id: string
  type: "weight_loss" | "savings" | "study" | "custom"
  title: string
  params: Record<string, unknown>
  answers: Record<string, unknown>
  framework: Record<string, unknown>
  status: string
  nudge_enabled: boolean
  created_at: string
  /** viewer 视角附带：共享档位（progress/detail）；owner 视角无此字段 */
  share_level?: string
  /** 详情/圈内列表可能附带：进度摘要（服务端按 viewer 粒度裁剪后给） */
  progress?: Record<string, unknown>
  /** 圈内列表/viewer 视角的所有者昵称（服务端 SQL 别名 owner_nickname） */
  owner_nickname?: string
  /** viewer 视角附带：当日是否已鞭策过 owner（前端据此置灰鞭策按钮） */
  viewer_nudged_today?: boolean
}

/** 今日计划条目：goal_id 空=自定义条目；source=adjust 是联动追加的调整条目（前端醒目样式） */
export interface PlanItem {
  id: string
  account_id?: string
  goal_id?: string | null
  date: string
  content: string
  kind: string
  source: "ai" | "custom" | "adjust"
  /** SQLite INTEGER 直出可能是 0/1，读取处用 Boolean() 归一 */
  done: boolean | number
  created_at?: string
}

/** 今日计划响应：generating 真值=后台懒生成中（语义照抄周报），前端 3s 轮询收敛 */
export interface TodayPlan {
  items: PlanItem[]
  generating: boolean | string
}

/** 记账条目：金额一律 INTEGER 分，展示层换算元 */
export interface Expense {
  id: string
  account_id?: string
  amount_fen: number
  category: string
  merchant: string
  note: string
  spent_at: string
  source?: string
  image_url?: string
  /** pending=待确认（识别结果），confirmed=已入账 */
  status?: string
  created_at?: string
}

/** 热量记录的菜品明细项 */
export interface CalorieItem {
  name: string
  kcal: number
  amount?: string
  /** 品牌（包装食品识别出才有，如 三养/白象） */
  brand?: string
  /** 模型估计的分量（克），识别时才有 */
  grams?: number
  /** 每 100g 热量（查表/联网命中时落库）：改克数按它重算 kcal，不用重新匹配 */
  kcal_per_100g?: number
  /** table=查《中国食物成分表》计算，model=模型估值兜底，
   *  staging=命中共建预数据库（待核实），web_pending=联网搜到（待认可），
   *  image_rag=以图搜图命中本账号历史确认图（复用菜名/单价） */
  source?: string
  /** source ∈ staging/web_pending 时指向 food_nutrition_staging 行，确认入账即计一次认可 */
  staging_id?: number
  /** 估值条目专属：联网数据已入库时的可升级信息（用户点击才按新单价重算） */
  upgrade?: { kcal_per_100g: number; kcal: number }
}

/** MET 运动等效：{running: {name: "跑步（8公里/小时）", met, minutes}, ...} */
export interface ExerciseEquiv {
  name: string
  met?: number
  minutes: number
}

/** 热量记录：exercise_equiv 为 MET 换算结果（运动 key → 等效分钟数） */
export interface CalorieEntry {
  id: string
  account_id?: string
  total_kcal: number
  items: CalorieItem[]
  exercise_equiv?: Record<string, ExerciseEquiv>
  note: string
  source?: string
  image_url?: string
  status?: string
  created_at: string
}

/** 鞭策留言：内容仅目标 owner（与发送者）可见，其余圈友不可见；归属在 account 级 */
export interface Nudge {
  id: string
  from_account_id: string
  message: string
  created_at: string
  from_nickname?: string
}

/** 计划鞭策留言：仅 owner 本人可查（服务端只查本人收件箱）；plan_date=被鞭策的日期 */
export interface PlanNudge {
  id: string
  from_account_id: string
  message: string
  plan_date: string
  created_at: string
  from_nickname?: string
}

/** 月账单响应：只列已入账；monthly_spendable_fen 仅存在存款目标时给 */
export interface LedgerMonth {
  month: string
  items: Expense[]
  /** 月度支出合计（分，只算正数支出；收入记负数账） */
  month_total_fen: number
  monthly_spendable_fen?: number
}

/** 某日热量响应：budget_kcal 仅存在减肥目标时给 */
export interface CalorieDay {
  date: string
  items: CalorieEntry[]
  consumed_kcal: number
  budget_kcal?: number
}

/** 营养共建预数据库（staging）行：verified=false 即「待核实」；approvals ≥ 3 晋升正式成分表 */
export interface StagedFood {
  id: number
  name: string
  kcal_per_100g: number
  protein_per_100g?: number | null
  fat_per_100g?: number | null
  cho_per_100g?: number | null
  /** user=用户手动添加，web=联网搜到 */
  source?: string
  verified?: boolean
  approvals?: number
  created_at?: string
}

/* ---------------- Self 共享与朋友任务（类别 × 圈子开关） ---------------- */

/** 共享类别：goal/plan 带 level 档位（progress/detail）；ledger/calorie 只有开关（level 恒 ''） */
export type SharingCategory = "goal" | "plan" | "ledger" | "calorie"

/** self_sharing 行：有行=共享，删行=关闭 */
export interface SharingItem {
  circle_id: string
  circle_name: string
  category: SharingCategory
  level: string
}

/** 朋友任务里共享出来的目标：progress 档只给进度摘要；detail 档附 params/answers/framework */
export interface FriendGoal {
  id: string
  type: Goal["type"]
  title: string
  status: string
  nudge_enabled: boolean
  created_at: string
  share_level: string
  progress?: Record<string, unknown>
  params?: Record<string, unknown>
  answers?: Record<string, unknown>
  framework?: Record<string, unknown>
}

/** 朋友任务里共享出来的今日计划：progress 档只有完成计数；detail 档附条目明细 */
export interface FriendPlan {
  date: string
  today_done: number
  today_total: number
  share_level: string
  items?: { id: string; content: string; kind: string; done: boolean | number }[]
}

/** 朋友任务成员卡：只出现共享了 goal/plan 任一类别的人；viewer_nudged_today 对人不对目标 */
export interface FriendMember {
  account_id: string
  nickname: string
  viewer_nudged_today: boolean
  goals?: FriendGoal[]
  plan?: FriendPlan
}

export interface FriendTasksResp {
  circle_id: string
  date: string
  members: FriendMember[]
}

/* ---------------- 情绪树洞（账号级私密对话，与圈子正交） ---------------- */

/** 树洞消息（L0 原文，history 接口正序全量返回） */
export interface TreeholeMessage {
  id: string
  role: "user" | "assistant"
  content: string
  image_url?: string
  created_at: string
}

/** 回复依据：检索命中的碎片/原子记忆摘抄（仅当轮 chat 响应返回，history 没有） */
export interface TreeholeCitation {
  kind: string
  id: string
  excerpt: string
}

/** 树洞整包响应：guardrail=true 表示触发干预话术（话术由后端给，前端照常展示） */
export interface TreeholeChatResp {
  reply: string
  citations: TreeholeCitation[]
  tools_used: string[]
  intent: string
  guardrail: boolean
}

/** 树洞人设卡：未设立时服务端合成默认倾听者并标 default=true（前端据此显示「去设立」引导）。
 * custom_prompt 为整段粘贴人设：非空时生成优先于模板字段（name 仍用于界面显示） */
export interface TreeholePersona {
  name: string
  personality: string
  speaking_style: string
  relationship: string
  background: string
  custom_prompt?: string
  default: boolean
}
