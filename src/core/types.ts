/** 平台无关的数据类型定义：web 与小程序（weapp）共用。 */

export interface Session {
  circle_id: string
  user_id: string
  nickname: string
  circle_name: string
  invite_code: string
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
