# Git + GitHub 开发教程（以本仓库 us-app 为例）

> 给「我们」项目的日常维护用。命令都在项目根目录 `/Users/user/Downloads/us-app` 下执行。
> 远程仓库：`https://github.com/lingmao233/usapp`

## 0. 三个先搞清楚的概念

**`.git` 在哪？** 它是隐藏目录，普通 `ls` 和访达默认不显示，但一直在项目根目录里。`ls -a` 或访达按 `Cmd + Shift + .` 可见。**不要手动删它**，删了等于丢掉全部提交历史。

**署名 ≠ 验证。** commit 时的 `user.name`/`user.email` 只是写进提交的文本署名，git 从不验证；真正的身份验证发生在 `git push`，由 GitHub 检查你的 token/SSH key（本机已存在 osxkeychain）。GitHub 再用提交里的 email 匹配账号显示头像——不匹配也照收，只是灰头像不关联。查署名：

```bash
git config user.name && git config user.email          # 本仓库配置（在 .git/config）
git log -1 --format="%an <%ae>"                        # 最近一次提交署的什么名
```

**三个区。** 工作区（你正在改的文件）→ 暂存区（`git add` 之后）→ 本地仓库（`git commit` 之后）→ 远程仓库（`git push` 之后）。几乎所有命令都是在搬动或比较这几层之间的内容。

---

## 1. 关联远程仓库（本项目已做好，仅作原理备忘）

```bash
# A. 本地已有代码，关联空远程仓库（本项目走的路）
git init -b main
git add -A && git commit -m "Initial commit"
git remote add origin https://github.com/lingmao233/usapp.git
git push -u origin main                     # -u 建立追踪，之后直接 git push

# B. 远程已有代码，克隆下来（换电脑时用这条）
git clone https://github.com/lingmao233/usapp.git
```

---

## 2. 常用指令速查（按场景）

### 看状态

```bash
git status                      # 现在哪些文件改了、暂存了没（最常用，没事就打）
git diff                        # 工作区 vs 暂存区：改了但还没 add 的内容
git diff --staged               # 暂存区 vs 上次提交：即将被 commit 的内容
git log --oneline -10           # 最近 10 条提交
git log --oneline --graph --all # 带分支图的全景
git show 提交号                  # 看某次提交改了什么
git blame 文件路径               # 每一行最后是谁改的
```

### 暂存与提交

```bash
git add 文件路径       # 只暂存指定文件
git add -A             # 暂存全部改动（含删除）
git commit -m "信息"   # 提交；信息写"做了什么"，如 "fix: 顶栏标题与导航重叠"
git commit --amend     # 修补最近一次提交（没 push 才能用）
```

### 拉取与推送

```bash
git fetch             # 只把远程状态拉下来看看，不动本地代码
git pull              # = fetch + 合并，相当于"同步远程新提交到本地"
git push              # 推当前分支到它的追踪分支
git push -f           # 强推（改写远程历史，只在确定无误时用，如覆盖占位 README）
```

### 分支

```bash
git branch                     # 看本地分支（当前分支带 *）
git branch -a                  # 连远程分支一起看
git switch -c feature/xxx      # 建分支并切过去（老写法 git checkout -b）
git switch main                # 切回主线
git branch -d feature/xxx      # 删已合并的本地分支
git push origin --delete feature/xxx   # 删远程分支
```

### 回退（按"改到哪一步了"分场景）

| 进度 | 命令 | 后果 |
|---|---|---|
| 改了还没 add | `git restore 文件`（或 `.` 全部） | 丢弃工作区改动，不可逆 |
| 已 add 没 commit | `git restore --staged 文件` | 退出暂存区，改动还在 |
| 已 commit 没 push，想修补 | `git commit --amend` | 改动并入上一次提交 |
| 已 commit 没 push，想撤销 | `git reset --soft HEAD~1` | 提交撤掉，改动回暂存区 |
| 同上，连改动都不要 | `git reset --hard HEAD~1` | **全扔，不可逆** |
| 已 push | `git revert HEAD` 然后 push | 生成一个"反向提交"抵消，不改写历史 |

口诀：**没 push 用 reset/amend，已 push 用 revert。**

### 救急

```bash
git stash           # 改到一半要切分支：先把现场收起来
git stash pop       # 回来恢复现场继续改
git reflog          # 后悔药：HEAD 的全部移动记录，reset 错了在这找回
```

### 标签（发版本用）

```bash
git tag v1.0 -m "第一个正式版"
git push origin v1.0        # 标签要单独推
```

---

## 3. GitHub 开发流：一个功能的完整生命周期

以"给碎片加个置顶功能"为例，走一遍标准流程：

### 第 1 步：开 Issue（可选但推荐）

GitHub 仓库页 → Issues → New issue，写清要做什么、为什么。编号比如是 `#3`。大功能先开 issue 讨论，小改动可跳过。

### 第 2 步：从最新 main 开分支

```bash
git switch main
git pull                          # 确保基于最新代码
git switch -c feature/pin-fragment
```

### 第 3 步：写代码，小步提交

```bash
# 改一点提交一点，每条信息说清一件事
git add server/app/api/fragments.py
git commit -m "feat: 碎片置顶 API"
git add src/pages/Wall.tsx
git commit -m "feat: 碎片墙置顶展示"
```

### 第 4 步：推分支，开 Pull Request

```bash
git push -u origin feature/pin-fragment
```

GitHub 仓库页顶部会出现黄色提示条「Compare & pull request」，点开：

- 标题写清楚做了什么，正文可写 `Closes #3`（合并后自动关对应 issue）
- 自己先在 Files changed 页把 diff 过一遍，常常能自己发现问题
- 点 Create pull request

### 第 5 步：合并 PR

网页上点 Merge pull request（三种方式选 **Squash and merge**：把分支上的多条小提交压成一条进 main，历史最干净）。合并后 GitHub 会提示 Delete branch，点掉。

### 第 6 步：本地同步

```bash
git switch main
git pull                          # 把刚合并的内容拉回本地
git branch -d feature/pin-fragment
```

> 一个人开发嫌麻烦，小改动直接在 main 上 `add/commit/push` 也行；分支+PR 的价值在于**每个功能有一段可回溯、可整体回退的历史**，改动一大就值得。

---

## 4. 和别人协作

### 让别人提交前必须经你审核

核心思路：**不给 main 的直接写权限，都走 PR，你审完再合**。三种情况：

**① 朋友偶尔提代码（最省事）**：不加协作者。对方在 GitHub 网页 **Fork** 你的仓库 → 在自己 Fork 里改 → 发 PR 到你的仓库。你在 PR 页面逐行审、评论、Merge 才生效，对方全程没有你的仓库写权限。

**② 固定几个人一起开发（私有仓库）**：Settings → Collaborators → Add people 加对方 GitHub 账号。约定每人开分支干活、发 PR，你审完再合。靠自觉。

**③ 强制拦截（分支保护规则）**：Settings → Branches → Add branch ruleset：pattern 填 `main`，勾 **Require a pull request before merging** + **Require approvals**（≥1）。之后任何人（包括你）都不能直接推 main。**注意：私有仓库开保护规则要 GitHub Pro/Team，公开仓库免费。**

### 你作为审核者的日常

- 仓库页 Pull requests 标签里看待审 PR，Files changed 页逐行看，行内可留评论
- 满意就 Merge；不满意就 Request changes，对方改完推同一分支，PR 自动更新

### 处理冲突

PR 页面显示 "This branch has conflicts" 时，说明分支和 main 改了同一处。本地解决：

```bash
git switch feature/xxx
git pull origin main        # 把 main 合进分支；冲突文件会标 <<<<<<< ======= >>>>>>>
# 手动编辑冲突文件，保留要的内容，删掉标记行
git add 冲突文件
git commit -m "merge: 解决与 main 的冲突"
git push                    # PR 自动变为可合并
```

### 换电脑/重装后恢复

```bash
git clone https://github.com/lingmao233/usapp.git
cd usapp
git config user.name "lingmao"                  # 署名要重配（仓库局部）
git config user.email "2325988520@qq.com"
# HTTPS 推送会在第一次 push 时要求 token；或把新机器的 SSH 公钥加进 GitHub
```

`.env`、数据库、上传的图片**不在仓库里**（gitignore 了），换机器要重新配置/另行备份。

---

## 5. 本项目的日常维护节奏

```bash
# 平时改代码（小改动直接主线）
git status && git add -A && git commit -m "..." && git push

# 大功能
git switch -c feature/xxx → 小步提交 → push 分支 → 网页开 PR → Squash merge → 本地 pull

# 线上更新（服务器上）
cd /opt/us-app && git pull && docker compose up -d --build
```
