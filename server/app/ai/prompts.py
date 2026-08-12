"""PRD 第六节的三段 Prompt，直接采用原文。"""

# ---------- 人格系统：预写人格库（圈与圈独立，任何成员可换） ----------

DEFAULT_PERSONA = "observer"

PERSONAS: dict[str, str] = {
    "observer": "朋友圈观察员：轻松幽默，像朋友之间的聊天，亲切自然",
    "sunshi": "损友：毒舌但暖心，爱吐槽，吐槽里全是关心",
    "shudong": "温柔树洞：细腻共情，像深夜电台，句子软",
    "weekly": "编辑部周刊：正经媒体腔的戏仿，拿小圈子的事当头条写",
    "laba": "村口大喇叭：热情外放爱起哄，感叹号多，自来熟",
}


def resolve_persona(preset: str, custom: str) -> str:
    """人格解析：自定义文本非空优先；否则查预设库，查不到回退默认观察员。"""
    custom = (custom or "").strip()
    if custom:
        return custom
    return PERSONAS.get(preset, PERSONAS[DEFAULT_PERSONA])

FRAGMENT_CLASSIFY_PROMPT = """你是一个内容分类专家。请分析以下用户输入，判断其类型和标签。

输入：{content}

请输出 JSON：
{{
    "type": "text|image|link|mood",
    "tags": ["标签1", "标签2"],
    "is_knowledge": true|false,
    "is_wish": true|false,
    "wish_category": "eat|go|learn|buy|do|null",
    "ai_summary": "一句话摘要"
}}

规则：
1. 链接/长文 → is_knowledge = true
2. "想做/想去/想吃/想学/想买" → is_wish = true
3. 标签要具体，不超过 3 个
4. ai_summary 不超过 20 字
"""

WEEKLY_REPORT_PROMPT = """请用这个人格的口吻写：{persona}

以下是本周圈子里大家分享的碎片，请生成一份有趣的「交集报告」。

碎片列表：
{fragments}

以下是大家最近说过的话，学习这种语感，可以引用或玩梗，但不要大段照抄：
{quotes}

请生成 Markdown 格式的报告，包含以下结构：

事实与猜测的分寸（这条决定报告质量）：
- 原文明确说了的（如"我在海边""我去了 XX"）：可以肯定地写。
- 原文只是提及或表达愿望（如"海边""想去海边"）：可以推测，但推测必须用猜测语气（"可能""说不定""我猜"），同一份报告里同类猜测点到为止——不要反复强调不确定，更不要写"一切以本人确认为准""请勿脑补"这类免责声明。
- 带「（愿望）」的是想做的事：可以猜"TA 很想去"，不能说"已经去了"。
- 带「[图片] 描述」的：描述就是图片真实内容，可直接引用；只有「[图片]」没有描述的，只能写"晒了一张照片"，严禁编造画面细节。

# 本周交集报告（{week_start} - {week_end}）

## 🎯 本周主题
（一句话总结本周大家关注什么）

## 🔗 关键连接
（发现人与人之间的隐性关联，比如"A 和 B 都在看同一类文章"）

## 📚 知识沉淀
（有价值的收藏，自动归档的内容）

## 🎯 愿望动态
（大家想做的事，匹配成功的共同愿望）

## 💡 AI 洞察
（有趣的观察，比如"最近大家焦虑值偏高，建议安排一次户外活动"）
"""

WISH_MATCH_PROMPT = """以下是几个人的愿望列表，请找出共同愿望并生成行动方案。

愿望列表：
{wishes}

请输出 JSON：
{{
    "common_wishes": [
        {{
            "content": "愿望描述",
            "matched_users": ["用户1", "用户2"],
            "suggestion": "行动建议",
            "confidence": 0.85
        }}
    ]
}}

规则：
1. 相似度超过 0.7 才认为是共同愿望
2. suggestion 要具体可行，包含时间、地点、预算
3. confidence 表示匹配置信度
"""

SUMMARY_PROMPT = """请把以下内容压缩成三句话以内的中文摘要，直接输出摘要本身，不要任何前缀：

{text}
"""

PLAN_PROMPT = """几个朋友有一个共同愿望：「{wish}」（参与人：{users}）。
请生成一份"一起去"行动方案，输出 JSON：
{{
    "time": "建议时间",
    "location": "建议地点",
    "budget": "预算估计",
    "steps": ["第一步", "第二步", "第三步"]
}}
方案要具体、轻松、可执行，像一个热心的朋友在帮忙张罗。
"""

USER_PROFILE_PROMPT = """你在为一个小圈子 App 蒸馏成员画像。以下是成员「{nickname}」近期的统计数据（JSON）：

{stats}

以下是 TA 近期公开发言的摘录：
{excerpts}

请输出 JSON 画像：
{{
    "topics": ["最近常聊的话题，至多 3 个"],
    "habit": "活跃习惯，一句话",
    "wish_leaning": "愿望倾向，一句话，没有就填 \\"暂无\\"",
    "summary": "一段两三句话的画像描述，温暖具体，像老朋友眼中的 TA",
    "style": {{
        "catchphrases": ["口头禅或高频口头表达，至多 2 个，没有就空数组"],
        "wording": "用词习惯，一句话",
        "emoji": "emoji 使用偏好，一句话",
        "sentence_length": "句子长短，一句话"
    }}
}}

规则：
1. 只依据统计数据与摘录说话，不编造
2. 数据很少时如实写"还在观察"，语气保持正向
3. style 只从公开发言摘录里总结语感；摘录为空时各字段填 \\"暂无\\"
"""

PAIR_SUMMARY_PROMPT = """你在为一个小圈子 App 撰写一对朋友的关系摘要。

A：{name_a}，B：{name_b}
共同主题：{topics}
经确认的共同愿望数：{wish_count} 个
关系信号强弱（仅供你把握详略，绝不可写进摘要）：{levels}

请输出一段两三句话的关系摘要，要求：
1. 正向叙事，温暖具体，像了解他们的老朋友在讲述
2. 绝不出现任何分数、等级、排名或"信号"字眼
3. 共同主题很少时，写"交集还在路上"式的鼓励，不硬凑
"""
