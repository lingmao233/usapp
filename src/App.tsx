import { useEffect, useState } from "react"
import { Menu } from "lucide-react"
import { Navigate, NavLink, Route, Routes } from "react-router"
import {
  api,
  clearAccountId,
  clearSession,
  loadAccountId,
  loadSession,
  type Session,
} from "@/lib/api"
import { enablePush, pushSupported, syncPushSubscription } from "@/lib/push"
import { copyText } from "@/lib/utils"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import CodeCustomizer from "@/components/CodeCustomizer"
import { DEFAULT_PERSONA_KEY, PERSONA_PRESETS, personaLabel } from "@/lib/persona"
import Onboarding from "@/pages/Onboarding"
import Wall from "@/pages/Wall"
import Knowledge from "@/pages/Knowledge"
import Wishes from "@/pages/Wishes"
import Graph from "@/pages/Graph"

const TABS: { to: string; label: string }[] = [
  { to: "/wall", label: "碎片墙" },
  { to: "/knowledge", label: "知识库" },
  { to: "/wishes", label: "愿望清单" },
  { to: "/graph", label: "关系" },
]

/** 顶栏"我的身份码"弹出层 */
function IdentityCode({ onClose }: { onClose: () => void }) {
  const [code, setCode] = useState<string | null>(null)
  const [nickname, setNickname] = useState("")
  const [copied, setCopied] = useState(false)
  const [loadError, setLoadError] = useState(false)
  const accountId = loadAccountId()

  useEffect(() => {
    if (!accountId) {
      setLoadError(true)
      return
    }
    api
      .getAccount(accountId)
      .then((res) => {
        setCode(res.recovery_code)
        setNickname(res.nickname)
      })
      .catch(() => setLoadError(true))
  }, [accountId])

  async function copy() {
    if (!code) return
    if (await copyText(code)) {
      setCopied(true)
    }
    /* 失败时身份码文本带 select-all，长按/全选手动复制兜底 */
  }

  return (
    <div
      className="fixed inset-0 z-20 flex items-center justify-center bg-black/20 px-6"
      onClick={onClose}
    >
      <div
        className="us-rise us-panel rounded-2xl p-6 w-full max-w-sm text-center"
        onClick={(e) => e.stopPropagation()}
      >
        <p className="us-serif text-lg mb-1">{nickname ? `${nickname} 的` : ""}身份恢复码</p>
        <p className="text-xs text-stone-500 mb-4">换设备登录就靠它，别发给圈外人</p>
        {code ? (
          <p className="us-serif text-3xl tracking-[0.3em] text-[#264653] mb-5 select-all">
            {code}
          </p>
        ) : loadError ? (
          <p className="text-sm text-red-700 mb-5">
            没拿到身份码，检查网络后重开这个弹层试试
          </p>
        ) : (
          <p className="text-sm text-stone-400 mb-5">加载中…</p>
        )}
        <div className="flex justify-center gap-3">
          <button className="us-btn" disabled={!code} onClick={copy}>
            {copied ? "已复制 ✓" : "复制"}
          </button>
          <button className="us-btn-ghost" onClick={onClose}>
            关闭
          </button>
        </div>
        {code && accountId && (
          <CodeCustomizer
            accountId={accountId}
            currentCode={code}
            onChanged={(c) => {
              setCode(c)
              setCopied(false)
            }}
          />
        )}
      </div>
    </div>
  )
}

/** 顶栏"圈子人格"弹层：查看/切换预设人格、编辑自定义文本（任何成员可改，只影响本圈） */
function PersonaDialog({
  session,
  open,
  onOpenChange,
}: {
  session: Session
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [preset, setPreset] = useState(DEFAULT_PERSONA_KEY)
  const [custom, setCustom] = useState("")
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  // 每次打开都重拉当前人格（别的成员可能刚换过）
  useEffect(() => {
    if (!open) return
    setLoaded(false)
    setLoadError(false)
    setError("")
    api
      .getCircle(session.circle_id)
      .then((c) => {
        setPreset(c.persona_preset || DEFAULT_PERSONA_KEY)
        setCustom(c.persona_custom || "")
        setLoaded(true)
      })
      .catch(() => setLoadError(true))
  }, [open, session.circle_id])

  async function save() {
    setBusy(true)
    setError("")
    try {
      await api.updatePersona(session.circle_id, session.user_id, preset, custom.trim())
      onOpenChange(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败，再试一次")
    } finally {
      setBusy(false)
    }
  }

  const customActive = custom.trim().length > 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="us-serif">圈子人格</DialogTitle>
          <DialogDescription>
            周报会用这个人格的口吻写。圈里任何人都能换，只影响本圈。
          </DialogDescription>
        </DialogHeader>
        {loaded ? (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap gap-2">
              {PERSONA_PRESETS.map((p) => (
                <button
                  key={p.key}
                  type="button"
                  title={p.desc}
                  onClick={() => setPreset(p.key)}
                  className={`rounded-full px-3.5 py-1.5 text-sm transition-colors duration-200 ${
                    preset === p.key && !customActive
                      ? "bg-[#161616] text-white"
                      : "text-[#264653] border border-[#264653]/20 hover:bg-[#264653]/8"
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <div>
              <label className="text-xs text-stone-500">
                自定义人格{customActive ? `（生效中，已盖过「${personaLabel(preset)}」）` : "（留空则用上面选的预设）"}
              </label>
              <textarea
                className="us-input mt-1.5 min-h-[72px]"
                placeholder="比如：像佟掌柜一样，精明又热乎"
                value={custom}
                onChange={(e) => setCustom(e.target.value)}
              />
            </div>
            {error && <p className="text-sm text-red-700">{error}</p>}
            <div className="flex justify-end gap-3">
              <button className="us-btn-ghost" onClick={() => onOpenChange(false)}>
                取消
              </button>
              <button className="us-btn" disabled={busy} onClick={save}>
                {busy ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        ) : loadError ? (
          <p className="text-sm text-red-700">没拿到圈子信息，检查网络后重开这个弹层试试</p>
        ) : (
          <p className="text-sm text-stone-400">加载中…</p>
        )}
      </DialogContent>
    </Dialog>
  )
}

export default function App() {
  const [session, setSession] = useState<Session | null>(() => loadSession())
  const [showIdentity, setShowIdentity] = useState(false)
  const [showPersona, setShowPersona] = useState(false)
  // 切换圈子中：保留当前 session，Onboarding 列表提供"返回当前圈子"出口
  const [switching, setSwitching] = useState(false)
  // 推送入口（第 5 期）：仅在支持 PushManager 且权限未决时显示；拒绝/开启后自然消失
  const [notifyVisible, setNotifyVisible] = useState(
    () => pushSupported() && Notification.permission === "default",
  )
  const [notifyBusy, setNotifyBusy] = useState(false)

  // 已授权设备静默换绑到当前身份（切换账号后保持推送可达）
  useEffect(() => {
    if (session) syncPushSubscription(session.user_id)
  }, [session])

  async function enableNotifications() {
    if (!session) return
    setNotifyBusy(true)
    try {
      await enablePush(session.user_id)
    } catch {
      /* 网络/SW 异常时入口保留，可重试 */
    } finally {
      setNotifyBusy(false)
      setNotifyVisible(pushSupported() && Notification.permission === "default")
    }
  }

  function switchCircle() {
    setSwitching(true)
  }

  function switchAccount() {
    clearSession()
    clearAccountId()
    setSession(null)
  }

  if (!session || switching) {
    return (
      <Onboarding
        onDone={(s) => {
          setSession(s)
          setSwitching(false)
        }}
        onCancel={session ? () => setSwitching(false) : undefined}
      />
    )
  }

  return (
    <div className="min-h-screen">
      {/* 顶栏：容器比正文宽一档（max-w-3xl），标题与导航不再挤在一起 */}
      <header className="sticky top-0 z-10 backdrop-blur-sm bg-[#F5F0E1]/85 border-b border-[#264653]/10">
        <div className="max-w-3xl mx-auto px-5 py-3 flex items-center justify-between gap-4">
          <div className="flex items-baseline gap-3 min-w-0">
            <h1 className="us-serif text-xl shrink-0">我们</h1>
            <span className="text-xs text-stone-400 truncate">
              {session.circle_name}
              {session.invite_code ? ` · 邀请码 ${session.invite_code}` : ""}
            </span>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <nav className="flex gap-1">
              {TABS.map((t) => (
                <NavLink
                  key={t.to}
                  to={t.to}
                  className={({ isActive }) =>
                    `rounded-full px-2 sm:px-3.5 py-1.5 text-sm transition-colors duration-200 ${
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
            {/* 桌面端：操作平铺（保持原布局）；移动端收进菜单 */}
            <div className="hidden md:flex items-center gap-3">
              <span className="text-sm text-[#264653] font-medium">
                {session.nickname}
              </span>
              <button
                className="text-xs text-stone-400 hover:text-[#264653] transition-colors"
                title="查看身份恢复码"
                onClick={() => setShowIdentity(true)}
              >
                我的身份码
              </button>
              <button
                className="text-xs text-stone-400 hover:text-[#264653] transition-colors"
                title="查看/切换圈子人格"
                onClick={() => setShowPersona(true)}
              >
                圈子人格
              </button>
              {notifyVisible && (
                <button
                  className="text-xs text-stone-400 hover:text-[#264653] transition-colors disabled:opacity-50"
                  title="有新评论/点赞时推送通知"
                  disabled={notifyBusy}
                  onClick={enableNotifications}
                >
                  {notifyBusy ? "开启中…" : "开启通知"}
                </button>
              )}
              <button
                className="text-xs text-stone-400 hover:text-[#264653] transition-colors"
                title="回到我的圈子列表"
                onClick={switchCircle}
              >
                切换圈子
              </button>
              <button
                className="text-xs text-stone-400 hover:text-[#264653] transition-colors"
                title="退出当前身份"
                onClick={switchAccount}
              >
                换个身份
              </button>
            </div>
            <div className="md:hidden">
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
                  <DropdownMenuItem className="py-2.5" onSelect={() => setShowIdentity(true)}>
                    我的身份码
                  </DropdownMenuItem>
                  <DropdownMenuItem className="py-2.5" onSelect={() => setShowPersona(true)}>
                    圈子人格
                  </DropdownMenuItem>
                  {notifyVisible && (
                    <DropdownMenuItem
                      className="py-2.5"
                      disabled={notifyBusy}
                      onSelect={enableNotifications}
                    >
                      {notifyBusy ? "开启中…" : "开启通知"}
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem className="py-2.5" onSelect={switchCircle}>
                    切换圈子
                  </DropdownMenuItem>
                  <DropdownMenuItem className="py-2.5" onSelect={switchAccount}>
                    换个身份
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </div>
      </header>

      {/* 页面（路由：默认跳转 /wall；Onboarding 不进路由，仍按 session 条件渲染） */}
      <main>
        <Routes>
          <Route path="/wall" element={<Wall session={session} />} />
          <Route path="/knowledge" element={<Knowledge session={session} />} />
          <Route path="/wishes" element={<Wishes session={session} />} />
          <Route path="/graph" element={<Graph session={session} />} />
          <Route path="*" element={<Navigate to="/wall" replace />} />
        </Routes>
      </main>

      {showIdentity && <IdentityCode onClose={() => setShowIdentity(false)} />}
      <PersonaDialog session={session} open={showPersona} onOpenChange={setShowPersona} />
    </div>
  )
}
