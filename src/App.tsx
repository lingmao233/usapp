import { useEffect, useState } from "react"
import { ArrowLeft, Menu, Search } from "lucide-react"
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router"
import {
  clearAccount,
  clearAccountId,
  clearDeviceToken,
  clearSession,
  loadAccount,
  loadSession,
  saveDeviceToken,
  saveSession,
  type AccountInfo,
  type Session,
} from "@/lib/api"
import { syncPushSubscription } from "@/lib/push"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import Onboarding from "@/pages/Onboarding"
import Landing from "@/pages/Landing"
import Wall from "@/pages/Wall"
import Wishes from "@/pages/Wishes"
import FriendsTasks from "@/pages/FriendsTasks"
import Graph from "@/pages/Graph"
import SearchPage from "@/pages/Search"
import Settings from "@/pages/Settings"
import Account from "@/pages/Account"
import Me from "@/pages/Me"
import GoalNew from "@/pages/GoalNew"
import GoalDetail from "@/pages/GoalDetail"
import Ledger from "@/pages/Ledger"
import Calories from "@/pages/Calories"
import TreeHole from "@/pages/TreeHole"

const TABS: { to: string; label: string }[] = [
  { to: "/wall", label: "碎片" },
  { to: "/wishes", label: "愿望清单" },
  { to: "/friends-tasks", label: "朋友任务" },
  { to: "/graph", label: "关系" },
]

/** 初始会话：把持久化的账号字段并进圈子 session（老存储没有这些字段） */
function initSession(): Session | null {
  const s = loadSession()
  const a = loadAccount()
  if (s && a && s.account_id !== a.account_id) {
    const merged: Session = {
      ...s,
      account_id: a.account_id,
      username: a.username,
      has_password: a.has_password,
    }
    saveSession(merged)
    return merged
  }
  return s
}

/** Self 系列页路由（圈内 /me 与无圈 Self 壳共用一份）；
 * 树洞同样只要 accountId，挂在这里两个壳都能进 */
function selfRoutes(accountId: string) {
  return (
    <>
      <Route path="/me" element={<Me accountId={accountId} />} />
      <Route path="/me/goals/new" element={<GoalNew accountId={accountId} />} />
      <Route path="/me/goals/:id" element={<GoalDetail accountId={accountId} />} />
      <Route path="/me/ledger" element={<Ledger accountId={accountId} />} />
      <Route path="/me/calories" element={<Calories accountId={accountId} />} />
      <Route path="/treehole" element={<TreeHole accountId={accountId} />} />
    </>
  )
}

export default function App() {
  const [account, setAccount] = useState<AccountInfo | null>(() => loadAccount())
  const [session, setSession] = useState<Session | null>(initSession)
  const navigate = useNavigate()
  const location = useLocation()
  // Self/树洞区由 URL 派生，不用 state + navigate 同帧驱动：
  // 同一 commit 里「改 state 挂载 Routes + navigate」会让新挂载的 Routes 按旧 location
  // 匹配，兜底 * 的 <Navigate> 把刚导航的目标页顶掉（见 docs/BUG记录.md BUG-013）
  const inSelfArea = location.pathname === "/treehole" || location.pathname.startsWith("/me")

  // 已授权设备静默换绑到当前圈内身份（切换账号/圈子后保持推送可达）
  useEffect(() => {
    if (session) syncPushSubscription(session.user_id)
  }, [session])

  function handleAuthed(a: AccountInfo) {
    setAccount(a)
  }

  function handleLogout() {
    clearSession()
    clearAccountId()
    clearAccount()
    clearDeviceToken()
    setSession(null)
    setAccount(null)
  }

  function enterCircle(s: Session) {
    // 建圈/入圈响应带的设备令牌随 session 汇到这里落盘（树洞等隐私接口的 Bearer 凭证）
    saveDeviceToken(s.device_token)
    saveSession(s)
    setSession(s)
    navigate("/wall")
  }

  // 未登录：账号登录/注册
  if (!account) {
    return <Onboarding onDone={handleAuthed} />
  }

  // 已登录未选圈：圈子 / Self / 情绪树洞 三选一
  if (!session && !inSelfArea) {
    return (
      <Landing
        account={account}
        onEnterCircle={enterCircle}
        onEnterSelf={() => navigate("/me")}
        onEnterTreehole={() => navigate("/treehole")}
        onLogout={handleLogout}
      />
    )
  }

  const accountId = account.account_id

  // Self 独立模式（无圈子 session）：极简顶栏 + /me 系列页
  if (!session) {
    return (
      <div className="min-h-screen">
        <header className="sticky top-0 z-10 backdrop-blur-sm bg-[#F5F0E1]/85 border-b border-[#264653]/10">
          <div className="max-w-3xl mx-auto px-5 py-3 flex items-center justify-between gap-4">
            <h1 className="us-serif text-xl shrink-0">
              我们 <span className="text-sm text-stone-400">· Self</span>
            </h1>
            <button
              className="us-btn-ghost text-xs border border-[#264653]/20"
              onClick={() => navigate("/")}
            >
              ← 返回入口
            </button>
          </div>
        </header>
        <main>
          <Routes>
            {selfRoutes(accountId)}
            <Route path="*" element={<Navigate to="/me" replace />} />
          </Routes>
        </main>
      </div>
    )
  }

  return (
    <div className="min-h-screen">
      {/* 顶栏：4 tab + 搜索 + 汉堡；nav 用 min-w-0 + overflow-x-auto 收缩，375px 不再重叠 */}
      <header className="sticky top-0 z-10 backdrop-blur-sm bg-[#F5F0E1]/85 border-b border-[#264653]/10">
        <div className="max-w-3xl mx-auto px-3 sm:px-5 py-3 flex items-center gap-2 sm:gap-4">
          <button
            className="p-1.5 -ml-1 text-[#264653] shrink-0 rounded-full hover:bg-[#264653]/8 transition-colors"
            title="返回入口"
            aria-label="返回入口"
            onClick={() => {
              clearSession()
              setSession(null)
              navigate("/")
            }}
          >
            <ArrowLeft size={18} />
          </button>
          <h1 className="us-serif text-xl shrink-0">我们</h1>
          <span className="hidden sm:inline text-xs text-stone-400 truncate min-w-0">
            {session.circle_name}
          </span>
          <nav className="flex gap-0.5 sm:gap-1 min-w-0 flex-1 justify-end overflow-x-auto">
            {TABS.map((t) => (
              <NavLink
                key={t.to}
                to={t.to}
                className={({ isActive }) =>
                  `whitespace-nowrap rounded-full px-2 sm:px-3.5 py-1.5 text-sm transition-colors duration-200 ${
                    isActive
                      ? "bg-[#161616] text-white"
                      : "text-[#264653] hover:bg-[#264653]/8"
                  }`
                }
              >
                {t.label}
              </NavLink>
            ))}
          </nav>
          <div className="flex items-center gap-1 shrink-0">
            <NavLink
              to="/search"
              title="搜索"
              aria-label="搜索"
              className={({ isActive }) =>
                `p-2 rounded-full transition-colors duration-200 ${
                  isActive ? "bg-[#161616] text-white" : "text-[#264653] hover:bg-[#264653]/8"
                }`
              }
            >
              <Search size={18} />
            </NavLink>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  className="p-2 -mr-2 text-[#264653]"
                  title="更多操作"
                  aria-label="更多操作"
                >
                  <Menu size={18} />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-[9rem]">
                <DropdownMenuLabel className="text-[#264653] font-medium">
                  {session.nickname}
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem className="py-2.5" onSelect={() => navigate("/settings")}>
                  设置
                </DropdownMenuItem>
                <DropdownMenuItem className="py-2.5" onSelect={() => navigate("/account")}>
                  个人
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </header>

      <main>
        <Routes>
          <Route path="/wall" element={<Wall session={session} accountId={accountId} />} />
          <Route path="/wishes" element={<Wishes session={session} />} />
          <Route path="/friends-tasks" element={<FriendsTasks session={session} />} />
          <Route path="/graph" element={<Graph session={session} />} />
          <Route path="/search" element={<SearchPage session={session} />} />
          <Route path="/settings" element={<Settings session={session} />} />
          <Route
            path="/account"
            element={
              <Account
                session={session}
                account={account}
                onEnterCircle={enterCircle}
                onLogout={handleLogout}
              />
            }
          />
          {/* 知识库入口并入搜索页 */}
          <Route path="/knowledge" element={<Navigate to="/search" replace />} />
          {selfRoutes(accountId)}
          <Route path="*" element={<Navigate to="/wall" replace />} />
        </Routes>
      </main>
    </div>
  )
}
