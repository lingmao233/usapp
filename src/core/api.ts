/** 平台无关的 API 客户端工厂：注入 RequestFn 即得完整 api 对象。
 *
 * web（src/lib/api.ts）与小程序（weapp/src/platform.ts）各自注入平台实现，
 * 方法签名与路径保持一一对应，保证两端行为一致、命中同一后端同一数据库。
 */
import type { RequestFn } from "./http"
import type {
  AccountCirclesResp,
  ChatMessage,
  Circle,
  Comment,
  CommonWish,
  Fragment,
  KnowledgeItem,
  PairGraph,
  RelatedFragment,
  Report,
  ReportMeta,
  Session,
  Wish,
  WishPlan,
} from "./types"

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
  }
}

export type Api = ReturnType<typeof createApi>
