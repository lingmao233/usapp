"""冒烟测试：fakes 确定性桩下打 FastAPI 完整链路（不触网、不烧 token）。

链路：建圈子 → 两用户加入 → 各发碎片（含链接、含愿望）→ 断言分类/归档/愿望
→ 相关碎片（跨用户）→ 语义搜索 → 共同愿望匹配 → 行动方案 → 周报 Markdown
→ 多圈子身份/恢复码 → 个人功能（目标 → 今日计划懒生成 → 打勾 → 记账/热量超预算联动 → 公开目标 → 鞭策限频）。

运行：.venv/bin/python scripts/smoke_test.py
"""
import os
import sys
import time

# 独立测试数据库 + 清空厂商 key（挡住 .env 回填），必须在 import app 之前设置
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "smoke_test.db")
os.environ["DB_PATH"] = os.path.abspath(DB_PATH)
for _k in ("LLM_API_KEY", "EMBEDDING_API_KEY", "VISION_API_KEY", "VISION_MODEL",
           "TREEHOLE_API_KEY", "TREEHOLE_BASE_URL", "TREEHOLE_MODEL", "TREEHOLE_WEB_SEARCH",
           "LLM_TEMPERATURE"):
    os.environ[_k] = ""
_SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _SERVER_DIR)
sys.path.insert(0, os.path.join(_SERVER_DIR, "tests"))

from fastapi.testclient import TestClient  # noqa: E402

import fakes  # noqa: E402
from app import ai  # noqa: E402
from app.main import app  # noqa: E402

fakes.install(ai)  # AI 门面整体换确定性桩（与 pytest conftest 同款接线）

# Windows GBK 控制台打不出 ✅/❌：强制 UTF-8 输出（不改编码直接崩 UnicodeEncodeError）
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

PASSED = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED
    assert cond, f"❌ {name} 失败 {extra}"
    PASSED += 1
    print(f"✅ {name}" + (f" — {extra}" if extra else ""))


def main() -> None:
    if os.path.exists(os.environ["DB_PATH"]):
        os.remove(os.environ["DB_PATH"])

    with TestClient(app) as client:
        run_all(client)


def run_all(client: TestClient) -> None:

    # 0. 健康检查
    r = client.get("/api/health")
    check("健康检查 /api/health", r.status_code == 200 and r.json()["status"] == "ok",
          f"AI 模式 {r.json().get('ai_mode')}")

    # 1. 建圈子 + 两个用户加入
    r = client.post("/api/circles", json={"name": "周末小队"})
    check("创建圈子", r.status_code == 200)
    circle = r.json()
    invite = circle["invite_code"]

    r1 = client.post("/api/circles/join", json={"invite_code": invite, "nickname": "阿澈"})
    r2 = client.post("/api/circles/join", json={"invite_code": invite, "nickname": "丫丫"})
    check("两个用户凭邀请码加入", r1.status_code == 200 and r2.status_code == 200)
    u1, u2 = r1.json(), r2.json()
    cid = circle["id"]

    # 2. 各发 3-5 条碎片（含一条链接、含愿望）
    posts_u1 = [
        "想去海边看日出",                       # 愿望 go
        "想学滑板呀",                           # 愿望 learn
        "看到一篇讲海边城市旅行攻略的文章 https://example.com/seaside-travel 先存着",  # 链接→知识
        "今天加班好累，只想躺着",
    ]
    posts_u2 = [
        "想去海边看日出，吹吹风",               # 与 u1 相似的愿望
        "想学滑板",                             # 与 u1 相似的愿望
        "最近单曲循环一首很温柔的歌",
    ]
    ids_u1, ids_u2 = [], []
    for content in posts_u1:
        r = client.post("/api/fragments", json={"circle_id": cid, "user_id": u1["user_id"], "content": content})
        assert r.status_code == 200, r.text
        ids_u1.append(r.json()["id"])
    for content in posts_u2:
        r = client.post("/api/fragments", json={"circle_id": cid, "user_id": u2["user_id"], "content": content})
        assert r.status_code == 200, r.text
        ids_u2.append(r.json()["id"])
    check("两个用户共发布 7 条碎片", len(ids_u1) == 4 and len(ids_u2) == 3)

    # 等待异步管线（TestClient 会等 BackgroundTasks，这里再兜底轮询 processed 标志）
    deadline = time.time() + 10
    while time.time() < deadline:
        frags = client.get("/api/fragments", params={"circle_id": cid}).json()["fragments"]
        if all(f["processed"] for f in frags):
            break
        time.sleep(0.2)
    frags = client.get("/api/fragments", params={"circle_id": cid}).json()["fragments"]
    check("异步管线处理完毕（processed 标志）", all(f["processed"] for f in frags))

    # 3. 断言分类 / 愿望 / 知识归档生效
    wish_frags = [f for f in frags if f["is_wish"]]
    check("愿望分类生效（is_wish ≥ 4）", len(wish_frags) >= 4,
          f"识别出 {len(wish_frags)} 条愿望")
    categories = {f["wish_category"] for f in wish_frags}
    check("愿望分类含 go/learn", "go" in categories and "learn" in categories, str(categories))

    knowledge = client.get("/api/knowledge", params={"circle_id": cid}).json()
    check("链接碎片自动归档知识库", len(knowledge["items"]) >= 1,
          f"归档 {len(knowledge['items'])} 条，标签 {knowledge['tags']}")
    check("知识条目有摘要和标签",
          all(i["summary"] for i in knowledge["items"]))

    wishes = client.get("/api/wishes", params={"circle_id": cid}).json()["wishes"]
    check("愿望自动进愿望表（≥ 4 条）", len(wishes) >= 4, f"共 {len(wishes)} 条")

    # 4. 相关碎片：跨用户、相似度 ≥ 0.7
    r = client.get(f"/api/fragments/{ids_u1[0]}/related")
    related = r.json()["related"]
    check("相关碎片返回跨用户结果", len(related) >= 1 and all(
        rel["user_id"] != u1["user_id"] for rel in related),
        f"{len(related)} 条，最高相似度 {related[0]['similarity'] if related else '-'}")

    # 5. 语义搜索
    r = client.post("/api/knowledge/search",
                    json={"query": "海边旅行攻略", "circle_id": cid, "top_k": 5})
    results = r.json()["results"]
    check("语义搜索有结果且相似度为正", len(results) >= 1 and results[0]["similarity"] > 0,
          f"top 相似度 {results[0]['similarity'] if results else '-'}")

    # 6. 共同愿望匹配（stale-while-revalidate：首轮可能 refreshing，轮询收敛；TestClient 内联跑后台任务）
    common = []
    for _ in range(20):
        cr = client.get("/api/wishes/common", params={"circle_id": cid}).json()
        common = cr["common_wishes"]
        if not cr.get("refreshing"):
            break
    check("共同愿望匹配出结果", len(common) >= 1,
          "; ".join(f"「{c['content']}」by {'&'.join(c['matched_users'])} ({c['confidence']})" for c in common))
    check("共同愿望是跨用户的", all(len(set(c["matched_users"])) >= 2 for c in common))

    # 7. 行动方案生成 + 缓存
    wish_id = wishes[0]["id"]
    r = client.post(f"/api/wishes/{wish_id}/plan")
    # 接口已改异步：未缓存返回 generating；TestClient 内联跑完后台任务后方案即落库
    check("生成「一起去」行动方案（异步受理）",
          r.json().get("status") == "generating" or "plan" in r.json())
    r2 = client.post(f"/api/wishes/{wish_id}/plan")
    plan = r2.json()
    check("方案缓存在 wishes.plan（第二次命中缓存）",
          plan.get("cached") is True and len(plan["plan"].get("steps", [])) >= 1,
          f"方案时间 {plan['plan'].get('time')}")

    # 8. 手动添加愿望
    r = client.post("/api/wishes", json={"circle_id": cid, "user_id": u2["user_id"], "content": "想吃一顿深夜火锅"})
    check("手动添加愿望", r.status_code == 200)

    # 9. 周报：手动生成返回 Markdown + 列表懒触发
    r = client.post("/api/reports/generate", json={"circle_id": cid})
    check("生成本周交集报告", r.status_code == 200 and r.json()["status"] in ("generated", "exists"))
    report_id = r.json()["report_id"]
    report = client.get(f"/api/reports/{report_id}").json()
    check("周报内容为 Markdown", report["content"].startswith("# 本周交集报告"),
          f"长度 {len(report['content'])} 字符")
    r = client.get("/api/reports", params={"circle_id": cid})
    check("报告历史列表", len(r.json()["reports"]) >= 1)

    # 10. 多圈子身份模型
    # 创建 account（通过建圈子隐式创建）→ 圈子 A
    r = client.post("/api/circles", json={"name": "圈子A", "nickname": "阿澈"})
    circle_a = r.json()
    check("建圈子 A 返回 account_id", r.status_code == 200 and bool(circle_a.get("account_id")))
    acc = circle_a["account_id"]

    # 别人建圈子 B
    r = client.post("/api/circles", json={"name": "圈子B", "nickname": "老周"})
    circle_b = r.json()
    check("圈子 B 由另一身份创建", circle_b["account_id"] != acc)

    # 同一 account 加入圈子 B（昵称留空沿用 account 昵称）
    r = client.post("/api/circles/join", json={
        "invite_code": circle_b["invite_code"], "nickname": "", "account_id": acc})
    join_b = r.json()
    check("同一 account 加入圈子 B", r.status_code == 200 and join_b["account_id"] == acc)
    check("昵称留空沿用 account 昵称", join_b["nickname"] == "阿澈", join_b["nickname"])

    # GET /accounts/{id}/circles 返回 2 个圈子
    r = client.get(f"/api/accounts/{acc}/circles")
    my_circles = r.json()
    check("accounts/circles 返回 2 个圈子", len(my_circles["circles"]) == 2,
          "、".join(c["circle_name"] for c in my_circles["circles"]))
    check("圈子列表含成员数/碎片数/加入时间",
          all("member_count" in c and "fragment_count" in c and "joined_at" in c
              for c in my_circles["circles"]))

    # 重复加入圈子 A → 幂等返回相同 user_id
    r = client.post("/api/circles/join", json={
        "invite_code": circle_a["invite_code"], "nickname": "改名试试", "account_id": acc})
    rejoin = r.json()
    check("重复加入圈子 A 幂等返回相同 user_id",
          rejoin["user_id"] == circle_a["user_id"] and rejoin.get("already_joined") is True)

    # 两个圈子的碎片流互相隔离
    client.post("/api/fragments", json={
        "circle_id": circle_a["id"], "user_id": circle_a["user_id"], "content": "这是 A 圈的秘密"})
    fa = client.get("/api/fragments", params={"circle_id": circle_a["id"]}).json()["fragments"]
    fb = client.get("/api/fragments", params={"circle_id": circle_b["id"]}).json()["fragments"]
    check("A/B 两圈碎片流互相隔离",
          any("A 圈的秘密" in f["content"] for f in fa)
          and not any("A 圈的秘密" in f["content"] for f in fb),
          f"A 圈 {len(fa)} 条 / B 圈 {len(fb)} 条")

    # 11. 身份恢复码 + 圈子内昵称唯一
    import re as _re
    code_a = circle_a.get("recovery_code")
    check("新建 account 返回 6 位恢复码",
          bool(code_a) and bool(_re.fullmatch(r"[A-HJ-KM-NP-Z2-9]{6}", code_a)), code_a or "")

    # 恢复码唯一性：此前流程创建的多个 account 恢复码互不相同
    codes = {c.get("recovery_code") for c in (circle, circle_a, circle_b) if c.get("recovery_code")}
    check("多个 account 恢复码全局唯一", len(codes) >= 2, str(codes))

    # 已有 account 再建圈不重复发恢复码
    r = client.post("/api/circles", json={"name": "圈子C", "account_id": acc})
    check("已有 account 建圈 recovery_code 为 null", r.json().get("recovery_code") is None)

    # claim 端点已下线：找回只走 /api/auth/reset（完整覆盖见 tests/test_recovery.py）
    r = client.post("/api/accounts/claim", json={"recovery_code": code_a})
    check("旧 claim 端点已下线（404/405）", r.status_code in (404, 405))

    # 圈子列表完整（3 个）
    claimed = client.get(f"/api/accounts/{acc}/circles").json()
    check("账号圈子列表完整（3 个）", len(claimed["circles"]) == 3,
          "、".join(c["circle_name"] for c in claimed["circles"]))

    # 昵称冲突：另一 account 用同名（带空格变体）加入圈子 A → 409
    r = client.post("/api/circles/join", json={
        "invite_code": circle_a["invite_code"], "nickname": "  阿澈  "})
    check("圈子内昵称冲突返回 409", r.status_code == 409 and "已经有人在用" in r.json()["detail"])

    # 同 account 幂等重进不受昵称唯一限制
    r = client.post("/api/circles/join", json={
        "invite_code": circle_a["invite_code"], "nickname": "阿澈", "account_id": acc})
    check("同 account 幂等重进不受昵称限制",
          r.status_code == 200 and r.json().get("already_joined") is True)

    # 换个名字可以正常加入
    r = client.post("/api/circles/join", json={
        "invite_code": circle_a["invite_code"], "nickname": "阿蓝"})
    check("换昵称后正常加入", r.status_code == 200 and bool(r.json().get("recovery_code")))

    # 12. 身份码增强：6 位新码、自定义、重置、存量 8 位兼容
    r = client.post("/api/circles", json={"name": "六位码圈", "nickname": "小六"})
    new_code = r.json()["recovery_code"]
    check("新生成的身份码为 6 位", bool(_re.fullmatch(r"[A-HJ-KM-NP-Z2-9]{6}", new_code)), new_code)
    acc6 = r.json()["account_id"]

    # 自定义身份码：任意字符原样存储（不再限定位数/字符集）；空与超长拒绝
    r = client.put(f"/api/accounts/{acc6}/recovery_code", json={"code": "km2pvq"})
    check("自定义身份码成功（原样存储，不再转大写）",
          r.status_code == 200 and r.json()["recovery_code"] == "km2pvq")
    r = client.put(f"/api/accounts/{acc6}/recovery_code", json={"code": "芝麻开门"})
    check("汉字身份码成功", r.status_code == 200 and r.json()["recovery_code"] == "芝麻开门")
    r = client.put(f"/api/accounts/{acc6}/recovery_code", json={"code": "   "})
    check("空身份码返回 400", r.status_code == 400)
    r = client.put(f"/api/accounts/{acc6}/recovery_code", json={"code": "长" * 65})
    check("超长身份码返回 400", r.status_code == 400)

    # 制造另一个 account 占用 PW8HJT，再验证冲突
    r = client.post("/api/circles", json={"name": "占位圈", "nickname": "占位"})
    acc_other = r.json()["account_id"]
    client.put(f"/api/accounts/{acc_other}/recovery_code", json={"code": "PW8HJT"})
    r = client.put(f"/api/accounts/{acc6}/recovery_code", json={"code": "PW8HJT"})
    check("身份码冲突返回 409", r.status_code == 409 and "已经被人用了" in r.json()["detail"])
    r = client.put(f"/api/accounts/{acc6}/recovery_code", json={"code": "pw8hjt"})
    check("身份码冲突大小写不敏感", r.status_code == 409)

    # 重置：旧码立即失效，新码可找回（claim 已下线，找回走 /api/auth/reset，需账号名登录体系）
    old_code = client.get(f"/api/accounts/{acc6}").json()["recovery_code"]
    r = client.post(f"/api/accounts/{acc6}/recovery_code/reset")
    reset_code = r.json()["recovery_code"]
    check("重置返回新的 6 位码",
          bool(_re.fullmatch(r"[A-HJ-KM-NP-Z2-9]{6}", reset_code)) and reset_code != old_code)

    # 找回全链路：注册带账号名的账号，自设汉字凭证 → auth/reset 换密码；重置后旧码失效、新码生效
    r = client.post("/api/auth/register", json={"username": "smoke-reset", "password": "p1"})
    acc_auth = r.json()["account_id"]
    r = client.put(f"/api/accounts/{acc_auth}/recovery_code", json={"code": "芝麻开门"})
    check("汉字身份码设置（找回账号）", r.status_code == 200)
    r = client.post("/api/auth/reset", json={
        "username": "smoke-reset", "recovery_code": "芝麻开门", "new_password": "p2"})
    check("汉字身份码可找回（auth/reset）", r.status_code == 200)
    r = client.post(f"/api/accounts/{acc_auth}/recovery_code/reset")
    new_auth_code = r.json()["recovery_code"]
    r = client.post("/api/auth/reset", json={"username": "smoke-reset", "recovery_code": "芝麻开门"})
    check("重置后旧码找回返回 403", r.status_code == 403)
    r = client.post("/api/auth/reset", json={
        "username": "smoke-reset", "recovery_code": new_auth_code, "new_password": "p3"})
    check("重置后新码可找回", r.status_code == 200)

    # 存量 8 位码仍可找回（直接改库模拟老数据；ASCII 大小写折叠）
    import sqlite3 as _sqlite3
    _conn = _sqlite3.connect(os.environ["DB_PATH"])
    _conn.execute("UPDATE accounts SET recovery_code = 'LEGACY88' WHERE id = ?", (acc_auth,))
    _conn.commit()
    _conn.close()
    r = client.post("/api/auth/reset", json={"username": "smoke-reset", "recovery_code": "legacy88"})
    check("存量 8 位码仍可找回（大小写不敏感）", r.status_code == 200)

    # 13. 个人功能（账号级 API）：目标 → 今日计划懒生成 → 打勾 → 记账/热量 → 超预算联动 → 共享 → 鞭策
    a1, a2 = u1["account_id"], u2["account_id"]
    r = client.post("/api/goals", json={
        "account_id": a1, "type": "weight_loss", "title": "减掉小肚腩",
        "params": {"target_weight_kg": 60, "days_left": 90},
        "answers": {"sex": "male", "weight_kg": 70, "height_cm": 175, "age": 30, "activity": "sedentary"}})
    goal = r.json()
    check("创建减肥目标（规则框架算预算）",
          r.status_code == 200 and goal["framework"].get("budget_kcal", 0) > 0,
          f"预算 {goal.get('framework', {}).get('budget_kcal')} kcal")
    gid = goal["id"]

    r = client.get("/api/plans/today", params={"account_id": a1})
    check("今日计划首次拉取触发懒生成", r.json()["generating"] is True)
    # TestClient 内联跑完 BackgroundTasks：重拉即收敛出确定性桩条目
    r = client.get("/api/plans/today", params={"account_id": a1})
    plan = r.json()
    ai_items = [i for i in plan["items"] if i["source"] == "ai"]
    check("今日计划收敛出 AI 条目",
          plan["generating"] is False and len(ai_items) >= 1,
          ai_items[0]["content"] if ai_items else "无条目")

    r = client.put(f"/api/plans/items/{ai_items[0]['id']}",
                   json={"account_id": a1, "done": True})
    check("计划条目打勾", r.status_code == 200)
    items = client.get("/api/plans/today", params={"account_id": a1}).json()["items"]
    check("打勾状态落库", any(i["id"] == ai_items[0]["id"] and i["done"] for i in items))

    r = client.post("/api/ledger/expenses", json={
        "account_id": a1, "amount_fen": 3550, "category": "餐饮", "merchant": "麦当劳"})
    check("手动记账直接入账", r.status_code == 200 and r.json()["status"] == "confirmed")
    month = plan["date"][:7]
    bill = client.get("/api/ledger/expenses",
                      params={"account_id": a1, "month": month}).json()
    check("月账单合计正确", bill["month_total_fen"] == 3550, f"当月 {len(bill['items'])} 笔")

    r = client.post("/api/calories", json={"account_id": a1, "total_kcal": 2000, "note": "放纵餐"})
    adj = r.json().get("adjustment")
    check("手动热量确认触发超预算联动", r.status_code == 200 and adj is not None,
          f"超 {adj['over_kcal']} kcal" if adj else "未联动")
    items = client.get("/api/plans/today", params={"account_id": a1}).json()["items"]
    adjust_items = [i for i in items if i["source"] == "adjust"]
    check("今日计划出现运动补偿 adjust 条目",
          len(adjust_items) == 1 and "超预算" in adjust_items[0]["content"],
          adjust_items[0]["content"][:40] if adjust_items else "无")

    # 共享是类别级开关（self_sharing）：开了 goal 共享，圈友才可见（progress 档裁掉明细）
    r = client.put("/api/self/sharing",
                   json={"account_id": a1, "circle_id": cid, "category": "goal"})
    check("开启目标共享（goal × 本圈）", r.status_code == 200)
    r = client.get(f"/api/goals/circle/{cid}", params={"account_id": a2})
    pub = [g for g in r.json()["goals"] if g["id"] == gid]
    check("圈内共享目标可见（progress 粒度）",
          r.status_code == 200 and len(pub) == 1 and "params" not in pub[0],
          pub[0]["title"] if pub else "未找到")

    r = client.post(f"/api/goals/{gid}/nudges", json={"account_id": a2, "message": "别喝奶茶了"})
    check("圈友鞭策成功", r.status_code == 200 and r.json()["status"] == "sent")
    r = client.post(f"/api/goals/{gid}/nudges", json={"account_id": a2, "message": "再来"})
    check("同日第二次鞭策 429 限频", r.status_code == 429)

    print(f"\n🎉 全部通过：{PASSED} 项断言")


if __name__ == "__main__":
    main()
