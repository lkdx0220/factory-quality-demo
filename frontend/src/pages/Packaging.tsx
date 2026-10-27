import { useMemo, useState } from 'react'
import { api, type Check, type Decision, type DecisionResult, type EvaluateResult, type SavedDecision, type ApiError } from '../api'
import { Badge, Code, Empty, ErrorBox, Loading, RawJson, toApiError, useAsync } from '../components/common'
import { DECISION, SN_STATUS, TEST_STATUS, reasonText } from '../labels'
import { duration, utc } from '../format'

export function DecisionBadge({ decision, size }: { decision: Decision; size?: 'lg' }) {
  const d = DECISION[decision]
  return (
    <span className={`badge tone-${d.tone} ${size === 'lg' ? 'badge-lg' : ''}`} title={decision}>
      <span className="badge-icon" aria-hidden>{d.icon}</span>
      {d.short}
      <span className="badge-code">{decision}</span>
    </span>
  )
}

type SavedFilter = 'all' | 'unsaved' | 'saved' | Decision

export function PackagingPage() {
  const checks = useAsync(() => api.checks(), [])
  const [decisionsVersion, setDecisionsVersion] = useState(0)
  const decisions = useAsync(() => api.decisions(), [decisionsVersion])
  const [selected, setSelected] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [savedFilter, setSavedFilter] = useState<SavedFilter>('all')
  const [day, setDay] = useState('')

  const savedByCheck = useMemo(() => new Map((decisions.data ?? []).map((d) => [d.check_id, d])), [decisions.data])
  const days = useMemo(() => [...new Set((checks.data ?? []).map((c) => c.check_at.slice(0, 10)))].sort(), [checks.data])
  const rows = useMemo(() => {
    const q = search.trim().toUpperCase()
    return (checks.data ?? []).filter((c) => {
      if (q && ![c.check_id, c.unit_id, c.requested_sn].some((v) => v.toUpperCase().includes(q))) return false
      if (day && !c.check_at.startsWith(day)) return false
      const saved = savedByCheck.get(c.check_id)
      if (savedFilter === 'unsaved') return !saved
      if (savedFilter === 'saved') return !!saved
      if (savedFilter !== 'all') return saved?.decision === savedFilter
      return true
    })
  }, [checks.data, search, day, savedFilter, savedByCheck])
  const current = checks.data?.find((c) => c.check_id === selected) ?? null
  const select = (checkId: string) => {
    setSelected(checkId)
    // On narrow screens the detail panel sits below the list.
    if (window.matchMedia('(max-width: 1180px)').matches) {
      requestAnimationFrame(() => document.querySelector('.detail-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>包装检查</h1>
          <p className="lead">选择一条检查请求，先查看只读的判定预览；确认依据后再由你点击“保存判定”写入本地演示数据库。</p>
        </div>
        <div className="legend" aria-label="判定结果说明">
          {(Object.keys(DECISION) as Decision[]).map((k) => (
            <div key={k} className="legend-item"><DecisionBadge decision={k} /><span>{DECISION[k].label}</span></div>
          ))}
        </div>
      </div>

      <div className="split">
        <section className="panel list-panel" aria-label="检查请求列表">
          <div className="toolbar">
            <input className="input" placeholder="搜索检查号 / 单件编号 / SN" value={search} onChange={(e) => setSearch(e.target.value)} />
            <select className="input" value={day} onChange={(e) => setDay(e.target.value)} aria-label="检查日期（UTC）">
              <option value="">全部日期（UTC）</option>
              {days.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
            <select className="input" value={savedFilter} onChange={(e) => setSavedFilter(e.target.value as SavedFilter)} aria-label="保存状态">
              <option value="all">全部保存状态</option>
              <option value="unsaved">尚未保存判定</option>
              <option value="saved">已保存判定</option>
              <option value="ALLOW">已保存：可放行</option>
              <option value="HOLD">已保存：扣留</option>
              <option value="REVIEW_BLOCKED">已保存：待核查</option>
            </select>
          </div>
          {checks.loading && !checks.data && <Loading />}
          {checks.error && <ErrorBox error={checks.error} onRetry={checks.retry} />}
          {decisions.error && !checks.error && <ErrorBox error={decisions.error} onRetry={decisions.retry} />}
          {checks.data && checks.data.length === 0 && <Empty title="没有检查请求">导入的快照中没有包装检查请求。</Empty>}
          {checks.data && checks.data.length > 0 && rows.length === 0 && <Empty title="没有符合筛选条件的请求">调整搜索词或筛选条件。</Empty>}
          {rows.length > 0 && (
            <div className="table-wrap">
              <table className="table table-click">
                <thead>
                  <tr><th>检查号</th><th>单件</th><th>请求 SN</th><th>检查时刻 (UTC)</th><th>已保存事件</th></tr>
                </thead>
                <tbody>
                  {rows.map((c) => {
                    const saved = savedByCheck.get(c.check_id)
                    return (
                      <tr key={c.check_id} className={c.check_id === selected ? 'selected' : ''} onClick={() => select(c.check_id)}
                        tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && select(c.check_id)}>
                        <td><Code>{c.check_id}</Code></td>
                        <td>{c.unit_id}</td>
                        <td>{c.requested_sn}</td>
                        <td className="nowrap">{c.check_at.slice(0, 16).replace('T', ' ')}</td>
                        <td>{saved ? <DecisionBadge decision={saved.decision} /> : <span className="muted">未保存</span>}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          {checks.data && <div className="table-foot muted small">共 {checks.data.length} 条，显示 {rows.length} 条 · 已保存 {savedByCheck.size} 条</div>}
        </section>

        <section className="panel detail-panel" aria-label="判定详情">
          {current
            ? <CheckDetail key={current.check_id} check={current} saved={savedByCheck.get(current.check_id)} savedLoading={decisions.loading}
                onSaved={() => setDecisionsVersion((v) => v + 1)} />
            : <Empty title="请在列表中选择检查请求">选择后会先计算只读预览，不会保存任何内容。</Empty>}
        </section>
      </div>
    </div>
  )
}

function CheckDetail({ check, saved, savedLoading, onSaved }: { check: Check; saved?: SavedDecision; savedLoading: boolean; onSaved: () => void }) {
  const preview = useAsync(() => api.preview(check.check_id), [check.check_id])
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<ApiError>()
  const [saveResult, setSaveResult] = useState<EvaluateResult>()

  async function save() {
    setSaving(true)
    setSaveError(undefined)
    try {
      setSaveResult(await api.evaluate(check.check_id))
      onSaved()
    } catch (e) {
      setSaveError(toApiError(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="detail">
      <div className="detail-head">
        <div>
          <div className="eyebrow">检查请求</div>
          <h2><Code>{check.check_id}</Code></h2>
        </div>
        <dl className="kv kv-inline">
          <dt>单件</dt><dd>{check.unit_id}</dd>
          <dt>请求 SN</dt><dd>{check.requested_sn}</dd>
          <dt>检查时刻</dt><dd>{utc(check.check_at)}</dd>
        </dl>
      </div>

      <div className="block block-preview">
        <div className="block-title">
          <h3>判定预览</h3>
          <Badge tone="info" icon="◌">只读计算 · 未保存</Badge>
        </div>
        {preview.loading && <Loading text="正在计算预览（只读）…" />}
        {preview.error && <ErrorBox error={preview.error} onRetry={preview.retry} />}
        {preview.data && !preview.loading && (
          <>
            {preview.data.persisted !== false && <div className="note note-warn">接口未返回 persisted=false，无法确认预览为只读，请检查后端版本。</div>}
            <DecisionView result={preview.data} checkAt={check.check_at} requestedSn={check.requested_sn} />
            <RawJson data={preview.data} title="原始 JSON（预览接口返回）" />
          </>
        )}
      </div>

      <div className="block block-saved">
        <div className="block-title">
          <h3>已保存事件</h3>
          <Badge tone="muted" icon="▣">本地演示数据库</Badge>
        </div>
        {savedLoading && !saved && <Loading text="正在读取已保存事件…" />}
        {!savedLoading && !saved && !saveResult && <div className="muted">该检查请求尚未保存判定事件。</div>}
        {saved && (
          <>
            <dl className="kv">
              <dt>保存的判定</dt><dd><DecisionBadge decision={saved.decision} /> {DECISION[saved.decision].label}</dd>
              <dt>事件编号</dt><dd><Code>{saved.decision_id}</Code></dd>
              <dt>规则版本</dt><dd><Code>{saved.policy_version}</Code></dd>
              <dt>计算执行时间</dt><dd>{utc(saved.evaluated_at)} <span className="muted small">（系统执行时间，不是业务检查时刻）</span></dd>
            </dl>
            {preview.data && saved.decision !== preview.data.decision && (
              <div className="note note-warn">已保存事件的判定（{saved.decision}）与当前预览（{preview.data.decision}）不一致，请核查。系统不会覆盖已保存事件。</div>
            )}
            <RawJson data={saved} title="原始 JSON（已保存事件）" />
          </>
        )}
        {saveResult && (
          <div className={`note ${saveResult.decision_event.reused ? 'note-info' : 'note-ok'}`} role="status">
            {saveResult.decision_event.reused
              ? <>该检查已有保存事件，本次复用了原事件 <Code>{saveResult.decision_event.decision_id}</Code>，没有新增记录。</>
              : <>已新建判定事件 <Code>{saveResult.decision_event.decision_id}</Code>。</>}
            {saveResult.mes_write_performed === false && <> 未向任何外部系统写入。</>}
          </div>
        )}
        {saveError && <ErrorBox error={saveError} />}

        <div className="save-bar">
          <button className="btn btn-primary" onClick={save} disabled={saving || !preview.data || !!preview.error}>
            {saving ? '正在保存…' : saved ? '再次保存（将复用原事件）' : '保存判定'}
          </button>
          <span className="small muted">
            保存仅在本地演示数据库追加一条判定事件记录；不会控制设备，也不会在任何实际系统中放行或扣留产品。重复保存会复用同一事件。
          </span>
        </div>
      </div>
    </div>
  )
}

export function DecisionView({ result, checkAt, requestedSn }: { result: DecisionResult; checkAt: string; requestedSn: string }) {
  const d = DECISION[result.decision]
  const id = result.identity
  return (
    <div className="decision-view">
      <div className={`decision-banner tone-${d.tone}`}>
        <span className="decision-icon" aria-hidden>{d.icon}</span>
        <div>
          <div className="decision-title">{d.label}<span className="badge-code">{result.decision}</span></div>
          <div className="decision-meaning">{d.meaning}</div>
        </div>
      </div>

      <h4>判定原因</h4>
      <ul className="reasons">
        {result.reason_codes.map((code) => {
          const r = reasonText(code)
          return (
            <li key={code}>
              {r.testType && <strong>{r.testType}：</strong>}{r.text}
              <Code>{code}</Code>
            </li>
          )
        })}
      </ul>

      <h4>编号绑定（截至检查时刻 {utc(checkAt)}）</h4>
      <dl className="kv">
        <dt>绑定状态</dt>
        <dd><Badge tone={SN_STATUS[id.sn_status]?.tone ?? 'warn'}>{SN_STATUS[id.sn_status]?.label ?? id.sn_status}</Badge> <Code>{id.sn_status}</Code></dd>
        <dt>请求 SN</dt><dd>{requestedSn}</dd>
        <dt>该单件已分配的 SN</dt><dd>{id.visible_sns.length ? id.visible_sns.join('、') : <span className="muted">无</span>}</dd>
        <dt>分配记录</dt><dd>{id.assignment_ids.length ? id.assignment_ids.map((a) => <Code key={a}>{a}</Code>) : <span className="muted">无</span>}</dd>
        <dt>批次 / 产品</dt><dd>{id.lot_id} / {id.product_id}</dd>
      </dl>
      {id.sn_status === 'CONFLICT' && (
        <div className="note note-warn small">绑定冲突可能来自“同一 SN 也分配给了其他单件”。可在“历史追溯”中按 SN 查询查看所有候选单件。</div>
      )}

      <h4>必需测试与采纳的结果</h4>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>测试阶段</th><th>测试要求</th><th>状态</th><th>采纳的记录</th><th>完成时间 (UTC)</th><th>距检查时刻 / 有效期</th></tr>
          </thead>
          <tbody>
            {result.tests.map((t) => {
              const s = TEST_STATUS[t.status] ?? { label: t.status, tone: 'muted', icon: '·' }
              return (
                <tr key={t.test_type}>
                  <td><strong>{t.test_type}</strong></td>
                  <td>
                    {t.required === null ? <span className="muted">—</span> : t.required ? '必需' : '不要求'}
                    {t.requirement_id && <div><Code>{t.requirement_id}</Code></div>}
                  </td>
                  <td><Badge tone={s.tone} icon={s.icon}>{s.label}</Badge>{t.expired && <> <Badge tone="warn" icon="⌛">已过期</Badge></>}</td>
                  <td>
                    {t.test_record_id ? (
                      <>
                        <Code>{t.test_record_id}</Code>
                        <div className="small muted">测试 {t.test_id} · 第 {t.attempt_no} 次尝试 · 版本 v{t.version}{(t.version ?? 1) > 1 && '（已更正）'}</div>
                      </>
                    ) : <span className="muted">{t.status === 'NOT_REQUIRED' ? '无需' : '无'}</span>}
                  </td>
                  <td className="nowrap">{t.completed_at ? t.completed_at.slice(0, 16).replace('T', ' ') : '—'}</td>
                  <td>
                    {duration(t.age_seconds)}
                    <div className="small muted">{t.max_age_seconds ? `有效期 ${duration(t.max_age_seconds)}` : t.required ? '无有效期限制' : ''}</div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="small muted">采纳规则由后端给出：对每个必需阶段，取检查时刻已记录的最新一次尝试的当前版本。前端只展示结果，不重新计算。</p>
    </div>
  )
}
