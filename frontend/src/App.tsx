import { useCallback, useEffect, useState } from 'react'
import { api, type Health } from './api'
import { DataContext } from './components/common'
import { PackagingPage } from './pages/Packaging'
import { TracePage } from './pages/Trace'
import { NeighborsPage } from './pages/Neighbors'
import { OverviewPage } from './pages/Overview'
import { QueryPlanPage } from './pages/QueryPlan'

const PAGES = [
  { id: 'packaging', label: '包装检查', hint: '判定预览与保存' },
  { id: 'trace', label: '历史追溯', hint: '按时刻回看单件/批次' },
  { id: 'neighbors', label: '邻批与质量分析', hint: '同设备前后作业、板卡集中性' },
  { id: 'overview', label: '数据概览', hint: '导入状态与计数' },
  { id: 'query-plan', label: '查询计划预检', hint: '范围、覆盖与显式确认' },
] as const
type PageId = (typeof PAGES)[number]['id']

function currentPage(): PageId {
  const id = window.location.hash.replace(/^#\/?/, '').split('?')[0]
  return (PAGES.find((p) => p.id === id)?.id ?? 'packaging') as PageId
}

type Link = { state: 'checking' } | { state: 'online'; health: Health } | { state: 'offline'; message: string }

export default function App() {
  const [page, setPage] = useState<PageId>(currentPage)
  const [version, setVersion] = useState(0)
  const [link, setLink] = useState<Link>({ state: 'checking' })
  const reload = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    const onHash = () => setPage(currentPage())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  // Read-only health probe for the header indicator.
  useEffect(() => {
    let alive = true
    const probe = () => api.health().then(
      (health) => alive && setLink({ state: 'online', health }),
      (error) => alive && setLink({ state: 'offline', message: String(error.message ?? error) }),
    )
    probe()
    const timer = window.setInterval(probe, 10000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [version])

  return (
    <DataContext.Provider value={{ version, reload }}>
      <div className="shell">
        <header className="topbar">
          <div className="brand">
            <span className="brand-mark" aria-hidden>QT</span>
            <div>
              <div className="brand-name">质量追溯工作台</div>
              <div className="brand-sub">虚构产品 · 虚构设备 · 本地演示</div>
            </div>
          </div>
          <div className="synthetic-flag" title="所有编号、事件、测试阶段和规则均为虚构演示数据">合成数据演示</div>
          <div className="topbar-right">
            <LinkIndicator link={link} />
          </div>
        </header>
        <nav className="sidenav" aria-label="主导航">
          {PAGES.map((p) => (
            <a key={p.id} href={`#/${p.id}`} className={`nav-item ${page === p.id ? 'active' : ''}`} aria-current={page === p.id ? 'page' : undefined}>
              <span className="nav-label">{p.label}</span>
              <span className="nav-hint">{p.hint}</span>
            </a>
          ))}
          <div className="nav-foot">
            判定结果仅保存在本地演示数据库，不控制任何设备，也不扣留实物产品。
          </div>
        </nav>
        <main className="content">
          {page === 'packaging' && <PackagingPage />}
          {page === 'trace' && <TracePage />}
          {page === 'neighbors' && <NeighborsPage />}
          {page === 'overview' && <OverviewPage />}
          {page === 'query-plan' && <QueryPlanPage />}
        </main>
      </div>
    </DataContext.Provider>
  )
}

function LinkIndicator({ link }: { link: Link }) {
  if (link.state === 'checking') return <span className="link link-checking">● 正在连接后端…</span>
  if (link.state === 'offline') return <span className="link link-offline" title={link.message}>⚡ 后端未连接</span>
  if (!link.health.database_exists) return <a className="link link-warn" href="#/overview">◐ 后端在线 · 数据库未初始化</a>
  return <span className="link link-online" title={`API ${link.health.version}`}>● 后端在线</span>
}
