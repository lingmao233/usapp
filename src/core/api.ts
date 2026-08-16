/** 平台无关的 API 客户端工厂：注入 RequestFn 即得完整 api 对象。
 *
 * web（src/lib/api.ts）与小程序（weapp/src/platform.ts）各自注入平台实现，
 * 方法签名与路径保持一一对应，保证两端行为一致、命中同一后端同一数据库。
 */
import type { RequestFn } from "./http"
import type {
  AccountCirclesResp,
  CalorieDay,
  CalorieEntry,
  ChatMessage,
  Circle,
  Comment,
  CommonWish,
  Expense,
  Fragment,
  Goal,
  KnowledgeItem,
  LedgerMonth,
  Nudge,
  PairGraph,
  RelatedFragment,
  Report,
  ReportMeta,
  Session,
  TodayPlan,
  Wish,
  WishPlan,
} from "./types"

/** 列表响应宽容解包：契约未钉死包装的列表接口，容忍裸数组与 {key: []} 两种形状。
 * 纯 JS 逻辑，两端通用（core 内禁止 DOM/浏览器 API）。 */
function unwrapList<T>(res: T[] | Record<string, unknown>, key: string): T[] {
  if (Array.isArray(res)) return res
  const v = (res as Record<string, unknown> | null)?.[key]
  return Array.isArray(v) ? (v as T[]) : []
}

export function createApi(req: RequestFn) {
  return {
    createCircle: (
      name: string,
      account_id?: string | null,
      nickname?: string,
      persona?: { persona_preset?: string; persona_custom?: string },
    ) =>
      req<{
        id: string
        name: string
        invite_code: string
        account_id: string
        user_id: string
        nickname: string
        persona_preset: string
        persona_custom: string
        recovery_code: string | null
      }>("/api/circles", {
        method: "POST",
        body: {
          name,
          account_id: account_id ?? undefined,
          nickname,
          persona_preset: persona?.persona_preset,
          persona_custom: persona?.persona_custom,
        },
      }),

    getCircle: (circle_id: string) => req<Circle>(`/api/circles/${circle_id}`),

    // 圈子人格：任何成员可换（user_id 做成员校验）；自定义文本非空时优先于预设
    updatePersona: (
      circle_id: string,
      user_id: string,
      persona_preset: string,
      persona_custom: string,
    ) =>
      req<Circle>(`/api/circles/${circle_id}/persona`, {
        method: "PUT",
        body: { user_id, persona_preset, persona_custom },
      }),

    joinCircle: (invite_code: string, nickname?: string, account_id?: string | null) =>
      req<Session & { account_id: string; already_joined?: boolean; recovery_code: string | null }>(
        "/api/circles/join",
        {
          method: "POST",
          body: { invite_code, nickname, account_id: account_id ?? undefined },
        },
      ),

    claimAccount: (recovery_code: string) =>
      req<{ account_id: string; nickname: string }>("/api/accounts/claim", {
        method: "POST",
        body: { recovery_code },
      }),

    // 按名字找回身份码：特定码核验后返回 圈子名+身份码 列表
    recoverLookup: (access_code: string, nickname: string) =>
      req<{ results: { circle_name: string; nickname: string; recovery_code: string }[] }>(
        "/api/accounts/recover-lookup",
        { method: "POST", body: { access_code, nickname } },
      ),

    getAccount: (account_id: string) =>
      req<{ account_id: string; nickname: string; recovery_code: string }>(
        `/api/accounts/${account_id}`,
      ),

    resetRecoveryCode: (account_id: string) =>
      req<{ account_id: string; recovery_code: string }>(
        `/api/accounts/${account_id}/recovery_code/reset`,
        { method: "POST" },
      ),

    setRecoveryCode: (account_id: string, code: string) =>
      req<{ account_id: string; recovery_code: string }>(
        `/api/accounts/${account_id}/recovery_code`,
        { method: "PUT", body: { code } },
      ),

    accountCircles: (account_id: string) =>
      req<AccountCirclesResp>(`/api/accounts/${account_id}/circles`),

    listMembers: (circle_id: string) =>
      req<{ members: { id: string; nickname: string }[] }>(`/api/circles/${circle_id}/members`),

    // 观看者（user_id）视角的关系图：共同主题/秘密提示已在服务端按身份过滤
    pairGraph: (circle_id: string, user_id: string) =>
      req<PairGraph>(`/api/circles/${circle_id}/graph?user_id=${user_id}`),

    // 图片上传（发图片）：原图 + 1600px 展示图两份，平台 request 实现负责包成 multipart
    uploadImage: (original: Blob, display?: Blob) =>
      req<{ url: string }>("/api/uploads", {
        method: "POST",
        body: { file: original, display },
      }),

    createFragment: (circle_id: string, user_id: string, content: string, visibility: "public" | "private" = "public", image_url?: string) =>
      req<{ id: string; status: string }>("/api/fragments", {
        method: "POST",
        body: { circle_id, user_id, content, visibility, image_url },
      }),

    // 删除（仅作者本人）：级联清评论/点赞/来源愿望/来源知识条目
    deleteFragment: (id: string, user_id: string) =>
      req<{ id: string; status: string }>(`/api/fragments/${id}?user_id=${user_id}`, {
        method: "DELETE",
      }),

    // user_id 用于服务端可见性判断（公开 + 我的隐私）；author 做作者筛选
    listFragments: (circle_id: string, user_id?: string, author?: string) =>
      req<{ fragments: Fragment[]; total: number }>(
        `/api/fragments?circle_id=${circle_id}&limit=100` +
          (user_id ? `&user_id=${user_id}` : "") +
          (author ? `&author=${author}` : ""),
      ),

    relatedFragments: (id: string, user_id?: string) =>
      req<{ related: RelatedFragment[] }>(
        `/api/fragments/${id}/related${user_id ? `?user_id=${user_id}` : ""}`,
      ),

    // 互动（第 4 期）：评论平铺列表（前端组楼中楼）；点赞 toggle 返回最新状态与总数
    listComments: (fragment_id: string) =>
      req<{ comments: Comment[] }>(`/api/fragments/${fragment_id}/comments`),

    addComment: (fragment_id: string, author_id: string, content: string, parent_id?: string) =>
      req<{ id: string; status: string }>(`/api/fragments/${fragment_id}/comments`, {
        method: "POST",
        body: { author_id, content, parent_id },
      }),

    toggleLike: (fragment_id: string, user_id: string) =>
      req<{ liked: boolean; like_count: number }>(`/api/fragments/${fragment_id}/like`, {
        method: "PUT",
        body: { user_id },
      }),

    listKnowledge: (circle_id: string, tag?: string) =>
      req<{ items: KnowledgeItem[]; tags: string[]; total: number }>(
        `/api/knowledge?circle_id=${circle_id}${tag ? `&tag=${encodeURIComponent(tag)}` : ""}`,
      ),

    searchKnowledge: (query: string, circle_id: string) =>
      req<{ results: KnowledgeItem[] }>("/api/knowledge/search", {
        method: "POST",
        body: { query, circle_id, top_k: 8 },
      }),

    listWishes: (circle_id: string, user_id?: string) =>
      req<{ wishes: Wish[] }>(
        `/api/wishes?circle_id=${circle_id}${user_id ? `&user_id=${user_id}` : ""}`,
      ),

    addWish: (circle_id: string, user_id: string, content: string, visibility: "public" | "private" = "public", image_url?: string) =>
      req<{ id: string }>("/api/wishes", {
        method: "POST",
        body: { circle_id, user_id, content, visibility, image_url },
      }),

    // 删除（仅作者本人）
    deleteWish: (id: string, user_id: string) =>
      req<{ id: string; status: string }>(`/api/wishes/${id}?user_id=${user_id}`, {
        method: "DELETE",
      }),

    // 勾选完成/取消（仅作者本人）：完成的愿望移出共同愿望匹配池，可逆
    toggleWishDone: (id: string, user_id: string, done: boolean) =>
      req<{ id: string; status: string }>(`/api/wishes/${id}/done`, {
        method: "PUT",
        body: { user_id, done },
      }),

    commonWishes: (circle_id: string) =>
      // stale-while-revalidate：refreshing=true 时 common_wishes 是旧结果，前端轮询收敛
      req<{ common_wishes: CommonWish[]; refreshing?: boolean }>(`/api/wishes/common?circle_id=${circle_id}`),

    // 方案：有缓存直接返回；否则转后台异步生成（status=generating），前端轮询 + Web Push 兜底
    wishPlan: (wish_id: string, user_id?: string) =>
      req<{ plan: WishPlan; participants?: string[]; cached?: boolean; status?: string }>(
        `/api/wishes/${wish_id}/plan`,
        { method: "POST", body: { user_id } },
      ),

    listReports: (circle_id: string) =>
      req<{
        reports: ReportMeta[]
        current_week: { week_start: string; week_end: string }
        generating: boolean | string
      }>(`/api/reports?circle_id=${circle_id}`),

    getReport: (id: string) => req<Report>(`/api/reports/${id}`),

    // 方案追问（轻量对话）：记录按 用户×愿望 一条线程持久化
    getPlanChat: (wish_id: string, user_id: string) =>
      req<{ messages: ChatMessage[] }>(`/api/chat/plan/${wish_id}?user_id=${user_id}`),

    sendPlanChat: (wish_id: string, user_id: string, message: string) =>
      req<{ messages: ChatMessage[] }>(`/api/chat/plan/${wish_id}`, {
        method: "POST",
        body: { user_id, message },
      }),

    /* ---------------- 个人功能：目标 ---------------- */

    // 建目标：params=目标参数、answers=问卷答案（均可空，服务端按通用默认值兜底并标 estimated）
    createGoal: (
      user_id: string,
      goal: {
        type: Goal["type"]
        title: string
        params: Record<string, unknown>
        answers: Record<string, unknown>
        visible_circle_ids: string[]
        detail_level: "summary" | "detail"
      },
    ) =>
      req<{ id: string; status: string; framework: Record<string, unknown> }>("/api/goals", {
        method: "POST",
        body: { user_id, ...goal },
      }),

    // 我的目标列表（服务端包 {goals}）
    listGoals: async (user_id: string) =>
      unwrapList<Goal>(
        await req<Goal[] | { goals?: Goal[] }>(`/api/goals?user_id=${user_id}`),
        "goals",
      ),

    // 详情含 progress/framework；viewer 过滤在服务端（非授权 404）
    getGoal: (id: string, viewer_id: string) =>
      req<Goal>(`/api/goals/${id}?viewer_id=${viewer_id}`),

    // 公开范围与粒度（仅 owner）；鞭策开关走单独的 nudge-toggle
    updateGoalSharing: (
      id: string,
      user_id: string,
      visible_circle_ids: string[],
      detail_level: "summary" | "detail",
    ) =>
      req<{ id?: string; status?: string }>(`/api/goals/${id}/sharing`, {
        method: "PUT",
        body: { user_id, visible_circle_ids, detail_level },
      }),

    // 鞭策开关（仅 owner）
    toggleNudge: (id: string, user_id: string, enabled: boolean) =>
      req<{ id?: string; nudge_enabled?: boolean }>(`/api/goals/${id}/nudge-toggle`, {
        method: "POST",
        body: { user_id, enabled },
      }),

    // 圈内公开目标（Wall「伙伴目标」区；服务端包 {goals}，恒 summary 粒度）
    circleGoals: async (circle_id: string, viewer_id: string) =>
      unwrapList<Goal>(
        await req<Goal[] | { goals?: Goal[] }>(
          `/api/goals/circle/${circle_id}?viewer_id=${viewer_id}`,
        ),
        "goals",
      ),

    /* ---------------- 个人功能：鞭策 ---------------- */

    // 发鞭策（body 字段名是 user_id=发起者；限频同人一天 1 次 429，屏蔽/关鞭策 403）
    sendNudge: (goal_id: string, from_user_id: string, message: string) =>
      req<{ id?: string; status?: string }>(`/api/goals/${goal_id}/nudges`, {
        method: "POST",
        body: { user_id: from_user_id, message },
      }),

    // 鞭策留言列表（owner 全见；服务端包 {count, nudges}，圈友只见 count）
    listNudges: async (goal_id: string, user_id: string) =>
      unwrapList<Nudge>(
        await req<Nudge[] | { nudges?: Nudge[] }>(
          `/api/goals/${goal_id}/nudges?user_id=${user_id}`,
        ),
        "nudges",
      ),

    // 按人屏蔽鞭策（仅 owner）
    blockNudgeUser: (user_id: string, blocked_user_id: string) =>
      req<{ status?: string }>("/api/nudge-blocks", {
        method: "POST",
        body: { user_id, blocked_user_id },
      }),

    /* ---------------- 个人功能：今日计划 ---------------- */

    // 今日清单：懒生成语义照抄周报——generating 真值时前端 3s 轮询收敛
    todayPlan: (user_id: string) => req<TodayPlan>(`/api/plans/today?user_id=${user_id}`),

    // 自定义条目（无 goal_id）；date 缺省服务端取今天
    addPlanItem: (user_id: string, content: string, date?: string) =>
      req<{ id?: string; status?: string }>("/api/plans/items", {
        method: "POST",
        body: { user_id, content, date },
      }),

    // 改内容/打勾
    updatePlanItem: (id: string, user_id: string, patch: { content?: string; done?: boolean }) =>
      req<{ id?: string; status?: string }>(`/api/plans/items/${id}`, {
        method: "PUT",
        body: { user_id, ...patch },
      }),

    deletePlanItem: (id: string, user_id: string) =>
      req<{ id?: string; status?: string }>(`/api/plans/items/${id}?user_id=${user_id}`, {
        method: "DELETE",
      }),

    /* ---------------- 个人功能：记账（金额一律分，收入记负数） ---------------- */

    // 小票/截图识别 → 待确认行已落库（包 {items}，一图多笔）；未配视觉模型时后端 400
    recognizeReceipt: async (user_id: string, image_url: string) =>
      unwrapList<Expense>(
        await req<Expense[] | { items?: Expense[] }>("/api/ledger/recognize", {
          method: "POST",
          body: { user_id, image_url },
        }),
        "items",
      ),

    // 手动记一笔（直接 confirmed）
    addExpense: (
      user_id: string,
      item: {
        amount_fen: number
        category: string
        merchant: string
        note: string
        spent_at: string
      },
    ) =>
      req<{ id?: string; status?: string }>("/api/ledger/expenses", {
        method: "POST",
        body: { user_id, ...item },
      }),

    // 确认待入账（带 id，可同时改金额/分类等字段；重复确认幂等）。一图多笔=逐笔调用
    confirmExpense: (
      id: string,
      user_id: string,
      patch: {
        amount_fen?: number
        category?: string
        merchant?: string
        note?: string
        spent_at?: string
      },
    ) =>
      req<{ id?: string; status?: string }>("/api/ledger/expenses", {
        method: "POST",
        body: { user_id, id, ...patch },
      }),

    // 月账单：{month, items, month_total_fen, monthly_spendable_fen?}
    listExpenses: (user_id: string, month: string) =>
      req<LedgerMonth>(`/api/ledger/expenses?user_id=${user_id}&month=${month}`),

    updateExpense: (
      id: string,
      user_id: string,
      patch: {
        amount_fen?: number
        category?: string
        merchant?: string
        note?: string
        spent_at?: string
      },
    ) =>
      req<{ id?: string; status?: string }>(`/api/ledger/expenses/${id}`, {
        method: "PUT",
        body: { user_id, ...patch },
      }),

    deleteExpense: (id: string, user_id: string) =>
      req<{ id?: string; status?: string }>(`/api/ledger/expenses/${id}?user_id=${user_id}`, {
        method: "DELETE",
      }),

    /* ---------------- 个人功能：热量 ---------------- */

    // 食物拍照识别（可附一句文字描述提准）→ 待确认行已落库（包 {entry}）；未配视觉模型时后端 400
    recognizeFood: async (user_id: string, image_url: string, hint?: string) => {
      const res = await req<CalorieEntry | { entry?: CalorieEntry }>("/api/calories/recognize", {
        method: "POST",
        body: { user_id, image_url, hint },
      })
      return (res as { entry?: CalorieEntry }).entry ?? (res as CalorieEntry)
    },

    // 手动录入（直接 confirmed；服务端触发超预算联动，adjustment 非空=已加调整条目）
    addCalorie: (user_id: string, total_kcal: number, note: string) =>
      req<{ id?: string; status?: string; adjustment?: { content?: string } | null }>(
        "/api/calories",
        { method: "POST", body: { user_id, total_kcal, note } },
      ),

    // 确认待入账（带 id，总热量可改，改了服务端重算运动等效；同样触发联动）
    confirmCalorie: (id: string, user_id: string, total_kcal?: number, note?: string) =>
      req<{ id?: string; status?: string; adjustment?: { content?: string } | null }>(
        "/api/calories",
        { method: "POST", body: { user_id, id, total_kcal, note } },
      ),

    // 按日查询：{date, items, consumed_kcal, budget_kcal?}
    listCalories: (user_id: string, date: string) =>
      req<CalorieDay>(`/api/calories?user_id=${user_id}&date=${date}`),
  }
}

export type Api = ReturnType<typeof createApi>
