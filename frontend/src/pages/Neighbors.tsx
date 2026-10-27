import { useEffect, useMemo, useState } from 'react'
import { api, type ApiError, type Concentration, type Neighbors, type Operation, type OperationStats } from '../api'
import { Badge, Code, Empty, ErrorBox, Loading, RawJson, UtcInput, toApiError, useAsync, useRequestGuard } from '../components/common'
import { UTC_PATTERN, pct, utc } from '../format'
import { classifyOperation, operationOrdinals, type OperationKind } from '../neighborQuery'
import { runGuarded } from '../requestGuard'

function OpKind({ kind }: { kind: OperationKind }) {
  if (kind.kind === 'unknown') return <Badge tone="warn" icon="?">无法分类</Badge>
  return kind.kind === 'original' ? <Badge tone="muted" icon="①">原作业</Badge> : <Badge tone="info" icon="↻">返工/重复 第{kind.ordinal}次</Badge>
}

/** A neighbor result together with the operation-list snapshot fetched for the same as_of. */
interface NeighborSnapshot { data: Neighbors; ordinals: Map<string, number> }

export function NeighborsPage() {
  const [asOf, setAsOf] = useState('2026-09-07T00:00:00Z')
  const validAsOf = UTC_PATTERN.test(asOf)
  // Tag the list with the as_of it was fetched for, so a list from a previous
  // time is never shown or used while the new one is loading.
  const operations = useAsync(async () => ({ asOf, items: validAsOf ? await api.operations({ as_of: asOf }) : [] as Operation[] }), [asOf])
  const [equipmentFilter, setEquipmentFilter] = useState('')
  const [stepFilter, setStepFilter] = useState('')
  const [anchor, setAnchor] = useState<string | null>(null)
  const [before, setBefore] = useState(3)
  const [after, setAfter] = useState(3)
  const [sameProduct, setSameProduct] = useState(false)
  const [result, setResult] = useState<{ loading: boolean; snapshot?: NeighborSnapshot; error?: ApiError }>({ loading: false })
  const [formError, setFormError] = useState('')
  const guard = useRequestGuard()

  const ops = useMemo(() => (operations.data?.asOf === asOf ? operations.data.items : []), [operations.data, asOf])
  const ordinal = useMemo(() => operationOrdinals(ops), [ops])
  const equipmentIds = useMemo(() => [...new Set(ops.map((o) => o.equipment_id))].sort(), [ops])
  const steps = useMemo(() => [...new Set(ops.map((o) => o.process_step))].sort(), [ops])
  const visibleOps = ops.filter((o) => (!equipmentFilter || o.equipment_id === equipmentFilter) && (!stepFilter || o.process_step === stepFilter))

  async function query() {
    if (!anchor) return setFormError('请先在上方列表中选择锚点作业。')
    if (!validAsOf) return setFormError('查询时刻需为完整的 UTC 日期和时间。')
    for (const [name, v] of [['向前数量', before], ['向后数量', after]] as const) {
      if (!Number.isInteger(v) || v < 0 || v > 100) return setFormError(`${name}需为 0–100 的整数。`)
    }
    setFormError('')
    setResult({ loading: true })
    // Classification uses an operation list fetched for this same as_of, and a
    // response is applied only if no newer query or time change happened since.
    const params = { as_of: asOf, before, after, same_product: sameProduct }
    await runGuarded(
      guard,
      async () => {
        const [data, snapshotOps] = await Promise.all([api.neighbors(anchor, params), api.operations({ as_of: params.as_of })])
        return { data, ordinals: operationOrdinals(snapshotOps) }
      },
      (snapshot) => setResult({ loading: false, snapshot }),
      (e) => setResult({ loading: false, error: toApiError(e) }),
    )
  }

  function changeAsOf(value: string) {
    guard.invalidate()
    setAsOf(value)
    setAnchor(null)
    setResult({ loading: false })
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>邻批与质量分析</h1>
          <p className="lead">选择一个作业作为锚点，查看同一设备、同一工序上紧挨着它的前后作业的测试表现，用于判断问题是否只集中在某几批。</p>
        </div>
      </div>

      <section className="panel anchor-panel">
        <div className="anchor-controls">
          <h3>1. 选择锚点作业</h3>
          <UtcInput label="查询时刻" value={asOf} onChange={changeAsOf} />
          <p className="small muted">列表只包含查询时刻已完成并已记录的作业（锚点必须满足此条件）。</p>
          <div className="toolbar">
            <select className="input" value={equipmentFilter} onChange={(e) => setEquipmentFilter(e.target.value)} aria-label="设备">
              <option value="">全部设备</option>
              {equipmentIds.map((id) => <option key={id}>{id}</option>)}
            </select>
            <select className="input" value={stepFilter} onChange={(e) => setStepFilter(e.target.value)} aria-label="工序">
              <option value="">全部工序</option>
              {steps.map((id) => <option key={id}>{id}</option>)}
            </select>
          </div>
          <p className="small muted">“返工/重复”由同批次同工序的作业先后次序得出；数据中没有显式的返工标记。</p>
        </div>
        <div className="anchor-list">
          {!validAsOf && <div className="field-error">请填写完整的查询时刻。</div>}
          {operations.loading && <Loading />}
          {operations.error && <ErrorBox error={operations.error} onRetry={operations.retry} />}
          {validAsOf && operations.data?.asOf === asOf && !operations.loading && ops.length === 0 && <Empty title="查询时刻没有已完成的作业">请把查询时刻调晚（样例作业集中在 2026-09-01 至 09-06）。</Empty>}
          {visibleOps.length > 0 && (
            <div className="table-wrap">
              <table className="table table-click">
                <thead><tr><th>作业</th><th>批次</th><th>工序</th><th>设备</th><th>出站 (UTC)</th><th>类型</th></tr></thead>
                <tbody>
                  {visibleOps.map((o) => (
                    <tr key={o.operation_id} className={anchor === o.operation_id ? 'selected' : ''} onClick={() => setAnchor(o.operation_id)}
                      tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && setAnchor(o.operation_id)}>
                      <td><Code>{o.operation_id}</Code></td><td>{o.lot_id}</td><td>{o.process_step}</td><td>{o.equipment_id}</td>
                      <td className="nowrap small">{o.track_out_at?.slice(0, 16).replace('T', ' ')}</td>
                      <td><OpKind kind={classifyOperation(ordinal, o.operation_id)} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

        <div className="stack">
          <section className="panel">
            <h3>2. 查询前后作业</h3>
            <div className="param-row">
              <label>锚点<div className="anchor-value">{anchor ? <Code>{anchor}</Code> : <span className="muted">未选择</span>}</div></label>
              <label>向前取（个）<input className="input input-num" type="number" min={0} max={100} value={before} onChange={(e) => setBefore(Number(e.target.value))} /></label>
              <label>向后取（个）<input className="input input-num" type="number" min={0} max={100} value={after} onChange={(e) => setAfter(Number(e.target.value))} /></label>
              <label className="check"><input type="checkbox" checked={sameProduct} onChange={(e) => setSameProduct(e.target.checked)} /> 仅同一产品</label>
              <button className="btn btn-primary" onClick={query} disabled={result.loading}>{result.loading ? '查询中…' : '查询邻批'}</button>
            </div>
            {formError && <div className="field-error" role="alert">{formError}</div>}
            {result.loading && <Loading />}
            {result.error && <ErrorBox error={result.error} onRetry={query} />}
            {!result.snapshot && !result.loading && !result.error && <Empty title="尚未查询">选择锚点后点击“查询邻批”。</Empty>}
            {result.snapshot && !result.loading && <NeighborResult snapshot={result.snapshot} />}
          </section>

          <ConcentrationPanel defaultEquipment={result.snapshot?.data.equipment_id ?? equipmentFilter} />
        </div>
    </div>
  )
}

type StatsRow = { pos: string; stats: OperationStats; anchor?: boolean; kind: OperationKind }

function NeighborResult({ snapshot: { data, ordinals } }: { snapshot: NeighborSnapshot }) {
  const rows: StatsRow[] = [
    ...data.before.map((s, i) => ({ pos: `前 ${data.before.length - i}`, stats: s })),
    { pos: '锚点', stats: data.anchor_stats, anchor: true },
    ...data.after.map((s, i) => ({ pos: `后 ${i + 1}`, stats: s })),
  ].map((r) => ({ ...r, kind: classifyOperation(ordinals, r.stats.operation_id) }))
  const original = rows.filter((r) => r.kind.kind === 'original')
  const repeat = rows.filter((r) => r.kind.kind === 'repeat')
  const unknown = rows.filter((r) => r.kind.kind === 'unknown')
  return (
    <div className="neighbor-result">
      <div className="asof-banner">
        <strong>{data.equipment_id} · {data.process_step} · 查询时刻 {utc(data.as_of)}</strong>
        <span>同设备同工序、查询时刻已完成的作业，按出站时间排序。{data.same_product_filter && '已限定为与锚点同一产品。'}</span>
      </div>
      {(data.before_insufficient || data.after_insufficient) && (
        <div className="note note-warn">
          {data.before_insufficient && <div>向前请求 {data.n_before} 个，实际只有 {data.before_count} 个符合条件的作业。</div>}
          {data.after_insufficient && <div>向后请求 {data.n_after} 个，实际只有 {data.after_count} 个符合条件的作业。</div>}
          数据不足部分不做补齐。
        </div>
      )}
      <StatsTable title="原作业" hint="批次在该工序的首次作业" rows={original} />
      <StatsTable title="返工 / 重复作业" hint="同批次同工序的再次作业，单独统计，不与原作业混算" rows={repeat} />
      {unknown.length > 0 && (
        <StatsTable title="无法分类" hint="同一查询时刻的作业列表中找不到这些作业，无法判断是否为重复作业" rows={unknown} />
      )}
      <div className="explain small">
        <strong>指标说明</strong>（均由后端计算）：分母 = 批次单件数；每件取该作业中最后一次尝试判为通过/失败/无效，没有任何尝试记为缺失。
        最终良率 = 最终通过 ÷ 分母，首次通过率 = 首次尝试即通过 ÷ 分母，覆盖率 = 已测 ÷ 分母。缺失不会被当作通过。
      </div>
      <RawJson data={data} />
    </div>
  )
}

function StatsTable({ title, hint, rows }: { title: string; hint: string; rows: StatsRow[] }) {
  return (
    <div className="stats-block">
      <h4>{title} <span className="muted small">{hint}</span></h4>
      {rows.length === 0 ? <div className="muted small">本次查询范围内没有此类作业。</div> : (
        <div className="table-wrap">
          <table className="table table-num">
            <thead>
              <tr>
                <th>位置</th><th>作业</th><th>批次 / 产品</th><th>出站 (UTC)</th>
                <th>分母</th><th>已测</th><th>首次通过</th><th>最终通过</th><th>失败</th><th>无效</th><th>缺失</th>
                <th>首次通过率</th><th>最终良率</th><th>覆盖率</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ pos, stats: s, anchor }) => (
                <tr key={s.operation_id} className={anchor ? 'anchor-row' : ''}>
                  <td>{anchor ? <Badge tone="info" icon="◆">锚点</Badge> : pos}</td>
                  <td><Code>{s.operation_id}</Code></td>
                  <td>{s.lot_id}<div className="small muted">{s.product_id}</div></td>
                  <td className="nowrap small">{s.track_out_at.slice(0, 16).replace('T', ' ')}</td>
                  <td>{s.denominator}</td>
                  <td>{s.tested_count}</td>
                  <td>{s.first_pass_count}</td>
                  <td>{s.final_pass_count}</td>
                  <td className={s.fail_count ? 'num-bad' : ''}>{s.fail_count}</td>
                  <td className={s.invalid_count ? 'num-warn' : ''}>{s.invalid_count}</td>
                  <td className={s.missing_count ? 'num-warn' : ''}>{s.missing_count}</td>
                  <td>{pct(s.first_pass_yield)}</td>
                  <td>{pct(s.final_yield)}</td>
                  <td>
                    {pct(s.coverage, 0)}
                    {s.coverage !== null && s.coverage < 1 && <div><Badge tone="warn" icon="◐">覆盖不足</Badge></div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function ConcentrationPanel({ defaultEquipment }: { defaultEquipment: string }) {
  const equipment = useAsync(() => api.equipment(), [])
  const [equipmentId, setEquipmentId] = useState('')
  const [start, setStart] = useState('2026-09-01T00:00:00Z')
  const [end, setEnd] = useState('2026-09-07T00:00:00Z')
  const [state, setState] = useState<{ loading: boolean; data?: Concentration; error?: ApiError; cancelled?: boolean }>({ loading: false })
  const [formError, setFormError] = useState('')
  const guard = useRequestGuard()
  const chosen = equipmentId || defaultEquipment
  // Changing equipment or window while a comparison is pending abandons it.
  // (`chosen` may also change via the neighbor query's equipment.)
  const pending = state.loading
  useEffect(() => {
    if (pending) {
      guard.invalidate()
      setState({ loading: false, cancelled: true })
    }
    // Only input changes should trigger this, not the pending flag itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chosen, start, end])

  async function run() {
    if (!chosen) return setFormError('请选择设备。')
    if (!UTC_PATTERN.test(start) || !UTC_PATTERN.test(end)) return setFormError('开始与结束时刻需为完整的 UTC 日期和时间。')
    if (start >= end) return setFormError('结束时刻必须晚于开始时刻。')
    setFormError('')
    setState({ loading: true })
    // Only the latest comparison may write; each result carries its own equipment and window.
    await runGuarded(
      guard,
      () => api.concentration({ equipment_id: chosen, start, end }),
      (data) => setState({ loading: false, data }),
      (e) => setState({ loading: false, error: toApiError(e) }),
    )
  }

  const maxRate = Math.max(0, ...(state.data?.groups ?? []).map((g) => g.fail_rate ?? 0))
  return (
    <section className="panel">
      <h3>3. 板卡集中性比较</h3>
      <p className="small muted">比较同一设备上不同载具位置（板卡 + 工位）在时间窗内的失败情况。必须同时看失败数和测试数：测试次数少时，失败率容易偏高。</p>
      <div className="param-row">
        <label>设备
          <select className="input" value={chosen} onChange={(e) => setEquipmentId(e.target.value)}>
            <option value="">请选择</option>
            {(equipment.data ?? []).map((e) => <option key={e.equipment_id} value={e.equipment_id}>{e.equipment_id} · {e.equipment_name}</option>)}
          </select>
        </label>
      </div>
      {equipment.error && <ErrorBox error={equipment.error} onRetry={equipment.retry} />}
      <div className="window-row">
        <UtcInput label="开始（含）" value={start} onChange={setStart} presets={[{ value: '2026-09-01T00:00:00Z', label: '09-01' }, { value: '2026-09-03T00:00:00Z', label: '09-03' }]} />
        <UtcInput label="结束（不含）" value={end} onChange={setEnd} presets={[{ value: '2026-09-03T00:00:00Z', label: '09-03' }, { value: '2026-09-07T00:00:00Z', label: '09-07' }]} />
      </div>
      <button className="btn btn-primary" onClick={run} disabled={state.loading}>{state.loading ? '计算中…' : '比较板卡'}</button>
      {formError && <div className="field-error" role="alert">{formError}</div>}
      {state.loading && <Loading />}
      {state.cancelled && <div className="note note-info" role="status">设备或时间窗在计算完成前被修改，未完成的比较已作废，其结果不会显示。请重新点击“比较板卡”。</div>}
      {state.error && <ErrorBox error={state.error} onRetry={run} />}
      {state.data && !state.loading && (
        <div className="concentration">
          <div className="small muted">
            统计范围（后端定义）：设备 {state.data.equipment_id} 上首个测试阶段的测试尝试，开始时间位于 [{utc(state.data.window.from)}, {utc(state.data.window.to)})，每个尝试取窗口结束时已记录的版本。
          </div>
          {state.data.groups.length === 0 ? <Empty title="该时间窗内没有测试尝试">不显示 0% 失败率，因为没有数据可比较。</Empty> : (
            <div className="table-wrap">
              <table className="table table-num">
                <thead><tr><th>载具绑定</th><th>板卡</th><th>工位</th><th>测试次数</th><th>失败次数</th><th>失败率</th><th className="bar-col">失败率对比</th></tr></thead>
                <tbody>
                  {state.data.groups.map((g) => (
                    <tr key={g.binding_id ?? 'none'}>
                      <td>{g.binding_id ? <Code>{g.binding_id}</Code> : <span className="muted">无载具绑定</span>}</td>
                      <td>{g.fixture_id ?? '—'}</td>
                      <td>{g.position_id ?? '—'}</td>
                      <td>{g.attempts}</td>
                      <td className={g.fail_attempts ? 'num-bad' : ''}>{g.fail_attempts}</td>
                      <td>{pct(g.fail_rate)}</td>
                      <td className="bar-col">
                        <div className="bar" aria-hidden><div className="bar-fill" style={{ width: maxRate ? `${((g.fail_rate ?? 0) / maxRate) * 100}%` : 0 }} /></div>
                        <span className="small">{g.fail_attempts} / {g.attempts}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="note note-info small">
            这是调查线索，不是根因结论。<span className="muted">（后端说明：<Code>{state.data.interpretation}</Code>）</span>
          </div>
          <RawJson data={state.data} />
        </div>
      )}
    </section>
  )
}
