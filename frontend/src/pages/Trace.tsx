import { useMemo, useState, type FormEvent } from 'react'
import { api, type ApiError, type LotTrace, type TraceAlarm, type UnitTrace } from '../api'
import { Badge, Code, Empty, ErrorBox, Loading, RawJson, UtcInput, toApiError, useAsync, useRequestGuard } from '../components/common'
import { ALARM_SCOPE, TEST_STATUS } from '../labels'
import { UTC_PATTERN, utc } from '../format'
import { runGuarded } from '../requestGuard'

type Mode = 'unit_id' | 'sn' | 'lot_id'
const MODES: { id: Mode; label: string; placeholder: string }[] = [
  { id: 'unit_id', label: '单件编号', placeholder: '例如 U-00021' },
  { id: 'sn', label: 'SN', placeholder: '例如 SYN-00028' },
  { id: 'lot_id', label: 'LOT 批次', placeholder: '例如 LOT-0003' },
]

interface Query { mode: Mode; value: string; asOf: string }

export function TracePage() {
  const [mode, setMode] = useState<Mode>('unit_id')
  const [value, setValue] = useState('U-00021')
  const [asOf, setAsOf] = useState('2026-09-07T00:00:00Z')
  const [state, setState] = useState<{ loading: boolean; query?: Query; data?: UnitTrace | LotTrace; error?: ApiError; cancelled?: boolean }>({ loading: false })
  const query = state.query ?? null
  const guard = useRequestGuard()
  const [formError, setFormError] = useState('')
  const units = useAsync(() => api.units(), [])
  const lots = useAsync(() => api.lots(), [])

  /** Editing an input while a query is pending abandons that query (its late response is ignored). */
  function edit<T>(setter: (v: T) => void) {
    return (v: T) => {
      if (state.loading) {
        guard.invalidate()
        setState({ loading: false, cancelled: true })
      }
      setter(v)
    }
  }

  async function run(q: Query) {
    setState({ loading: true })
    // The query is stored with its own result, so a late response from an
    // earlier query can neither show nor be described by newer inputs.
    await runGuarded(
      guard,
      () => api.trace(q.asOf, { [q.mode]: q.value }),
      (data) => setState({ loading: false, query: q, data }),
      (e) => setState({ loading: false, query: q, error: toApiError(e) }),
    )
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    const v = value.trim()
    if (!v) return setFormError('请输入要查询的编号。')
    if (!UTC_PATTERN.test(asOf)) return setFormError('查询时刻需为完整的 UTC 日期和时间。')
    setFormError('')
    run({ mode, value: v, asOf })
  }

  const suggestions = mode === 'unit_id' ? (units.data ?? []).map((u) => u.unit_id) : mode === 'lot_id' ? (lots.data ?? []).map((l) => l.lot_id) : []

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>历史追溯</h1>
          <p className="lead">按单件编号、SN 或批次查询，并指定一个 UTC 查询时刻。结果只包含该时刻之前已经记录到系统的信息；之后补录或更正的数据不会出现。</p>
        </div>
      </div>

      <form className="panel query-form" onSubmit={submit}>
        <fieldset className="mode-field">
          <legend>查询对象</legend>
          <div className="segmented" role="radiogroup">
            {MODES.map((m) => (
              <button type="button" role="radio" aria-checked={mode === m.id} key={m.id} className={mode === m.id ? 'on' : ''}
                onClick={() => { edit(setMode)(m.id); setValue('') }}>{m.label}</button>
            ))}
          </div>
          <input className="input input-id" list="trace-suggestions" value={value} onChange={(e) => edit(setValue)(e.target.value)}
            placeholder={MODES.find((m) => m.id === mode)!.placeholder} aria-label="查询编号" />
          <datalist id="trace-suggestions">{suggestions.map((s) => <option key={s} value={s} />)}</datalist>
        </fieldset>
        <UtcInput label="查询时刻" value={asOf} onChange={edit(setAsOf)} />
        <div className="form-actions">
          <button className="btn btn-primary" type="submit" disabled={state.loading}>{state.loading ? '查询中…' : '查询'}</button>
          {formError && <div className="field-error" role="alert">{formError}</div>}
        </div>
      </form>

      {state.loading && <Loading text="正在按查询时刻读取…" />}
      {state.error && query && (
        state.error.code === 'AMBIGUOUS_SN'
          ? <AmbiguousSn error={state.error} query={query} onPick={(unitId) => { setMode('unit_id'); setValue(unitId); run({ mode: 'unit_id', value: unitId, asOf: query.asOf }) }} />
          : <ErrorBox error={state.error} onRetry={() => run(query)} />
      )}
      {state.cancelled && <div className="note note-info" role="status">查询条件在请求完成前被修改，未完成的查询已作废，其结果不会显示。请重新点击“查询”。</div>}
      {!state.loading && !state.error && !state.data && <Empty title="尚未查询">填写编号与查询时刻后点击“查询”。默认时刻位于样例数据期间（2026-09）。</Empty>}
      {state.data && query && !state.loading && (
        <>
          <div className="asof-banner">
            <strong>查询时刻 {utc(state.data.as_of)}</strong>
            <span>以下展示的是该时刻系统中已知的信息。迟到的记录、之后的更正和之后结束的报警时间均不提前显示。</span>
          </div>
          {'units' in state.data ? <LotView trace={state.data} /> : <UnitView trace={state.data} />}
          <RawJson data={state.data} />
        </>
      )}
    </div>
  )
}

function AmbiguousSn({ error, query, onPick }: { error: ApiError; query: Query; onPick: (unitId: string) => void }) {
  const details = error.details as { sn?: string; unit_ids?: string[] } | undefined
  return (
    <div className="panel conflict-panel" role="alert">
      <div className="block-title">
        <h3>身份冲突：SN 对应多个单件</h3>
        <Badge tone="warn" icon="?">需要人工选择</Badge>
      </div>
      <p>截至 {utc(query.asOf)}，SN <Code>{details?.sn ?? query.value}</Code> 被分配给了以下多个单件。系统不会自动猜测是哪一件，请核对后选择要查看的单件。</p>
      <div className="candidates">
        {(details?.unit_ids ?? []).map((id) => (
          <div className="candidate" key={id}>
            <div className="candidate-id">{id}</div>
            <button className="btn btn-ghost" onClick={() => onPick(id)}>按此单件编号查看</button>
          </div>
        ))}
      </div>
      <div className="small muted">原始错误：<Code>{error.code}</Code> {error.message}</div>
    </div>
  )
}

type TimelineEvent = { at: string; kind: string; tone: string; title: string; detail?: string; code?: string }

function timeline(trace: UnitTrace): TimelineEvent[] {
  const events: TimelineEvent[] = []
  for (const op of trace.operations) {
    events.push({ at: op.track_in_at, kind: '作业进站', tone: 'info', title: `${op.process_step} 作业开始 · ${op.equipment_id}`, code: op.operation_id })
    if (op.track_out_at) events.push({ at: op.track_out_at, kind: '作业出站', tone: 'info', title: `${op.process_step} 作业完成 · ${op.equipment_id}`, code: op.operation_id })
  }
  for (const t of trace.tests) {
    const s = TEST_STATUS[t.outcome]
    const where = t.binding ? `${t.equipment_id} · 载具 ${t.binding.fixture_id} / ${t.binding.position_id}` : `${t.equipment_id} · 无载具绑定`
    events.push({ at: t.completed_at, kind: '测试尝试', tone: s?.tone ?? 'muted', title: `${t.test_type} 第 ${t.attempt_no} 次：${s?.label ?? t.outcome}${t.version > 1 ? `（版本 v${t.version}，已更正）` : ''}`, detail: where, code: t.test_record_id })
  }
  for (const a of trace.visible_sn) events.push({ at: a.assigned_at, kind: 'SN 分配', tone: 'muted', title: `分配 SN ${a.sn}`, code: a.assignment_id })
  for (const a of trace.unit_linked_alarms) events.push({ at: a.started_at, kind: '关联报警', tone: 'warn', title: `${ALARM_SCOPE[a.scope] ?? a.scope}报警 ${a.alarm_code}`, code: a.alarm_id })
  return events.sort((x, y) => x.at.localeCompare(y.at))
}

function UnitView({ trace }: { trace: UnitTrace }) {
  const events = useMemo(() => timeline(trace), [trace])
  const multipleSn = new Set(trace.visible_sn.map((s) => s.sn)).size > 1
  return (
    <div className="trace-unit">
      <div className="summary-row">
        <div className="summary-item"><span>单件</span><strong>{trace.unit_id}</strong></div>
        <div className="summary-item"><span>批次</span><strong>{trace.lot_id}</strong></div>
        <div className="summary-item"><span>产品</span><strong>{trace.product_id}</strong></div>
        <div className="summary-item"><span>已分配 SN</span><strong>{trace.visible_sn.length ? trace.visible_sn.map((s) => s.sn).join('、') : '尚未分配'}</strong></div>
        <div className="summary-item"><span>测试尝试</span><strong>{trace.distinct_test_count} 次</strong></div>
      </div>
      {multipleSn && <div className="note note-warn">该单件在查询时刻对应多个 SN，存在身份冲突。</div>}

      <div className="trace-grid">
        <section className="panel">
          <h3>时间线</h3>
          {events.length === 0 ? <Empty title="查询时刻没有已记录的事件" /> : (
            <ol className="timeline">
              {events.map((e, i) => (
                <li key={i} className={`tl-item tone-${e.tone}`}>
                  <div className="tl-time">{e.at.slice(0, 16).replace('T', ' ')}</div>
                  <div className="tl-dot" aria-hidden />
                  <div className="tl-body">
                    <div><span className="tl-kind">{e.kind}</span>{e.title}</div>
                    {e.detail && <div className="small muted">{e.detail}</div>}
                    {e.code && <Code>{e.code}</Code>}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </section>

        <div className="stack">
          <section className="panel">
            <h3>所在批次的作业</h3>
            {trace.operations.length === 0 ? <Empty title="查询时刻尚无作业记录" /> : (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>作业</th><th>工序</th><th>设备</th><th>进站</th><th>出站</th><th>状态</th></tr></thead>
                  <tbody>
                    {trace.operations.map((o) => (
                      <tr key={o.operation_id}>
                        <td><Code>{o.operation_id}</Code></td><td>{o.process_step}</td><td>{o.equipment_id}</td>
                        <td className="nowrap">{o.track_in_at.slice(0, 16).replace('T', ' ')}</td>
                        <td className="nowrap">{o.track_out_at ? o.track_out_at.slice(0, 16).replace('T', ' ') : '—'}</td>
                        <td>{o.status === 'completed' ? <Badge tone="ok" icon="✓">已完成</Badge> : <Badge tone="info" icon="…">进行中</Badge>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="panel">
            <h3>测试尝试与更正版本</h3>
            {trace.tests.length === 0 ? <Empty title="查询时刻没有已记录的测试结果" /> : (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>阶段 / 尝试</th><th>结果</th><th>记录 / 版本</th><th>作业 · 设备</th><th>载具位置</th><th>完成 / 记录时间</th><th>投递</th></tr></thead>
                  <tbody>
                    {trace.tests.map((t) => {
                      const s = TEST_STATUS[t.outcome]
                      const deliveries = trace.delivery_counts[t.test_record_id] ?? 0
                      return (
                        <tr key={t.test_id}>
                          <td><strong>{t.test_type}</strong> · 第 {t.attempt_no} 次<div className="small muted">{t.test_id}</div></td>
                          <td><Badge tone={s?.tone ?? 'muted'} icon={s?.icon}>{s?.label ?? t.outcome}</Badge></td>
                          <td><Code>{t.test_record_id}</Code><div className="small">v{t.version}{t.version > 1 && <Badge tone="info">已更正</Badge>}</div></td>
                          <td>{t.operation_id}<div className="small muted">{t.equipment_id}</div></td>
                          <td>{t.binding ? <>{t.binding.fixture_id} / {t.binding.position_id}<div className="small muted">{t.binding_id}</div></> : t.binding_id ? <span className="muted">{t.binding_id}（查询时刻未登记）</span> : <span className="muted">无</span>}</td>
                          <td className="nowrap small">{t.completed_at.slice(0, 16).replace('T', ' ')}<div className="muted">记录 {t.recorded_at.slice(0, 16).replace('T', ' ')}</div></td>
                          <td>{deliveries} 次{deliveries > 1 && <div className="small muted">含重复投递，已按记录去重</div>}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <p className="small muted">每次测试尝试只显示查询时刻已记录的最新版本。版本号大于 1 表示该结果曾被更正；接口不返回被替代的旧版本内容。</p>
          </section>
        </div>
      </div>

      <div className="alarm-grid">
        <AlarmList title="与本单件关联的报警" hint="报警对象是本单件、本单件使用过的载具位置，或本批次的作业。" alarms={trace.unit_linked_alarms} />
        <AlarmList title="设备背景报警（仅供参考）" hint="本单件测试过的设备上的设备级报警，不代表与本单件直接相关。" alarms={trace.equipment_context_alarms} context />
      </div>
      <p className="small muted">报警记录是调查线索，不等于失效根因。</p>
    </div>
  )
}

function AlarmList({ title, hint, alarms, context }: { title: string; hint: string; alarms: TraceAlarm[]; context?: boolean }) {
  return (
    <section className={`panel ${context ? 'panel-muted' : ''}`}>
      <h3>{title}</h3>
      <p className="small muted">{hint}</p>
      {alarms.length === 0 ? <div className="muted">查询时刻无此类报警记录。</div> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>报警</th><th>对象</th><th>开始</th><th>结束</th>{context && <th>与本件测试时段</th>}</tr></thead>
            <tbody>
              {alarms.map((a) => (
                <tr key={a.alarm_id}>
                  <td><Code>{a.alarm_id}</Code><div className="small muted">{a.alarm_code}</div></td>
                  <td>{ALARM_SCOPE[a.scope] ?? a.scope}{a.equipment_id && <div className="small muted">{a.equipment_id}</div>}</td>
                  <td className="nowrap small">{a.started_at.slice(0, 16).replace('T', ' ')}</td>
                  <td className="nowrap small">{a.ended_at ? a.ended_at.slice(0, 16).replace('T', ' ') : <span className="muted">查询时刻未结束或未知</span>}</td>
                  {context && <td>{a.overlaps_unit_activity ? <Badge tone="warn" icon="◐">时段重叠</Badge> : <Badge tone="muted" icon="○">不重叠</Badge>}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function LotView({ trace }: { trace: LotTrace }) {
  const [open, setOpen] = useState<string | null>(null)
  const stages = useMemo(() => [...new Set(trace.units.flatMap((u) => u.tests.map((t) => t.test_type)))].sort(), [trace])
  const ops = trace.units[0]?.operations ?? []
  return (
    <div className="trace-lot">
      <div className="summary-row">
        <div className="summary-item"><span>批次</span><strong>{trace.lot_id}</strong></div>
        <div className="summary-item"><span>查询时刻已创建的单件</span><strong>{trace.unit_count} 件</strong></div>
        <div className="summary-item"><span>批次作业</span><strong>{ops.length} 个</strong></div>
      </div>
      {trace.unit_count === 0 ? <Empty title="查询时刻该批次尚无单件" /> : (
        <section className="panel">
          <h3>批次内单件</h3>
          <p className="small muted">每格显示该阶段最近一次尝试的结果及尝试次数。点击行展开该单件的完整追溯。</p>
          <div className="table-wrap">
            <table className="table table-click">
              <thead><tr><th>单件</th><th>SN</th>{stages.map((s) => <th key={s}>{s}</th>)}<th>关联报警</th></tr></thead>
              <tbody>
                {trace.units.map((u) => (
                  <LotRow key={u.unit_id} unit={u} stages={stages} open={open === u.unit_id} onToggle={() => setOpen(open === u.unit_id ? null : u.unit_id)} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}

function LotRow({ unit, stages, open, onToggle }: { unit: UnitTrace; stages: string[]; open: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className={open ? 'selected' : ''} onClick={onToggle} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onToggle()}>
        <td><span aria-hidden>{open ? '▾' : '▸'}</span> {unit.unit_id}</td>
        <td>{unit.visible_sn.length ? unit.visible_sn.map((s) => s.sn).join('、') : <span className="muted">未分配</span>}</td>
        {stages.map((stage) => {
          const tries = unit.tests.filter((t) => t.test_type === stage)
          if (!tries.length) return <td key={stage}><span className="muted">无记录</span></td>
          const last = tries[tries.length - 1]
          const s = TEST_STATUS[last.outcome]
          return <td key={stage}><Badge tone={s?.tone ?? 'muted'} icon={s?.icon}>{s?.label ?? last.outcome}</Badge>{tries.length > 1 && <span className="small muted"> ×{tries.length}</span>}</td>
        })}
        <td>{unit.unit_linked_alarms.length ? <Badge tone="warn" icon="!">{unit.unit_linked_alarms.length}</Badge> : <span className="muted">0</span>}</td>
      </tr>
      {open && (
        <tr className="expand-row"><td colSpan={stages.length + 3}><UnitView trace={unit} /></td></tr>
      )}
    </>
  )
}
