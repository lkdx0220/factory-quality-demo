import { useState } from 'react'
import { type ApiError } from '../api'
import { planApi, type PlanCard, type ConfirmedPlan, type PlanMembers, type EvidenceExecution } from '../planApi'
import { Badge, Code, ErrorBox, RawJson, UtcInput, toApiError, useRequestGuard } from '../components/common'
import { FactsPanel } from '../components/FactsPanel'
import { NaturalLanguagePlanner } from '../components/NaturalLanguagePlanner'

const TIMES = [
  { label: '完整覆盖内', value: '2026-09-07T00:00:00Z' },
  { label: '恰覆盖边界', value: '2026-09-11T23:59:59Z' },
  { label: '超出覆盖', value: '2026-09-12T00:00:00Z' },
]

export function QueryPlanPage() {
  const [inputMode, setInputMode] = useState<'manual' | 'natural'>('manual')
  const [lot, setLot] = useState('LOT-0001')
  const [asOf, setAsOf] = useState(TIMES[0].value)
  const [original, setOriginal] = useState('截至指定时刻，列出这个 LOT 的全部可见作业。')
  const [card, setCard] = useState<PlanCard>()
  const [plan, setPlan] = useState<ConfirmedPlan>()
  const [members, setMembers] = useState<PlanMembers>()
  const [artifact, setArtifact] = useState<Record<string, unknown>>()
  const [execution, setExecution] = useState<EvidenceExecution>()
  const [ledger, setLedger] = useState<Record<string, unknown>>()
  const [error, setError] = useState<ApiError>()
  const [busy, setBusy] = useState(false)
  const guard = useRequestGuard()

  function edit(action: () => void) {
    guard.invalidate()
    action()
    setBusy(false)
    setCard(undefined)
    setPlan(undefined)
    setMembers(undefined)
    setArtifact(undefined)
    setExecution(undefined)
    setLedger(undefined)
    setError(undefined)
  }

  async function run<T,>(work: () => Promise<T>, apply: (result: T) => void) {
    const token = guard.begin()
    setBusy(true)
    setError(undefined)
    try {
      const result = await work()
      if (guard.isCurrent(token)) apply(result)
    } catch (e) {
      if (guard.isCurrent(token)) {
        const failure = toApiError(e)
        setError(failure)
        setLedger(undefined)
        setArtifact(undefined)
        setExecution(current => current ? { ...current, evidence: [], execution_complete: false,
          access_state: 'blocked', access_error_code: failure.code } : undefined)
        if (failure.status === 403) { setCard(undefined); setPlan(undefined); setMembers(undefined); setExecution(undefined) }
      }
    } finally { if (guard.isCurrent(token)) setBusy(false) }
  }

  async function create() {
    setCard(undefined); setPlan(undefined); setMembers(undefined); setArtifact(undefined); setExecution(undefined); setLedger(undefined)
    await run(() => planApi.create({ intent_key: 'lot_operation_inventory', original_input: original, lot_id: lot, as_of: asOf }), setCard)
  }

  async function loadMembers(offset = 0) {
    if (!card) return
    await run(() => planApi.members(plan?.members_url ?? card.members_url, offset), setMembers)
  }

  async function confirm() {
    if (!card) return
    await run(() => planApi.confirm(card), result => { setPlan(result); setArtifact(undefined) })
  }

  async function showExport() {
    if (!card) return
    await run(() => planApi.export(plan?.export_url ?? card.export_url), setArtifact)
  }

  function applyExecution(result: EvidenceExecution) {
    setExecution(result)
    setLedger(undefined)
  }

  const executing = execution?.state === 'queued' || execution?.state === 'running'
  const executionLabels = { queued: '等待执行', running: '执行中', completed: '必需证据执行完成', failed: '执行失败', cancelled: '已取消', interrupted: '服务中断，未重跑' }

  return <div className="page">
    <div className="page-head"><div><h1>查询计划预检</h1><p className="lead">先把“查谁、查到何时、范围是否完整”变成可核对的计划。自然语言与表单草稿共用同一条确认和发布链。</p></div></div>
    <div className="note note-info">本页仅支持单个 LOT 的历史作业清单。确认、执行及事实校验完成后，可生成固定模板的正式清单答案；不判断产品质量。现有历史追溯等页面仍是独立的确定性查询。</div>
    <div className="toolbar" aria-label="草稿输入方式"><button className={`btn ${inputMode === 'manual' ? 'btn-primary' : ''}`} aria-pressed={inputMode === 'manual'} onClick={() => edit(() => setInputMode('manual'))}>表单草稿</button><button className={`btn ${inputMode === 'natural' ? 'btn-primary' : ''}`} aria-pressed={inputMode === 'natural'} onClick={() => edit(() => setInputMode('natural'))}>自然语言规划</button></div>
    {inputMode === 'natural' && <NaturalLanguagePlanner onReset={() => edit(() => {})} onCard={result => { setOriginal(result.original_input); setLot(result.scope.lot_id); setAsOf(result.scope.as_of); setCard(result) }} />}
    {inputMode === 'manual' && <section className="panel stack">
      <label className="mode-field">用户输入（仅作为原话保存；当前不自动理解）
        <input className="input" aria-label="用户输入" maxLength={2000} value={original} onChange={e => edit(() => setOriginal(e.target.value))} />
      </label>
      <div className="query-form">
        <label className="mode-field">LOT 编号<input className="input" aria-label="计划 LOT 编号" value={lot} onChange={e => edit(() => setLot(e.target.value))} /></label>
        <UtcInput label="计划历史时刻" value={asOf} onChange={v => edit(() => setAsOf(v))} presets={TIMES} />
        <button className="btn btn-primary" disabled={busy || !lot || !asOf} onClick={create}>{busy ? '正在预检…' : '生成范围确认卡'}</button>
      </div>
    </section>}
    {error && <ErrorBox error={error} />}
    {card && <>
      <section className="panel stack">
        <div><Badge tone={card.can_confirm ? 'info' : 'warn'} icon={card.can_confirm ? '◉' : '!'}>{plan ? '范围已确认（预检阶段记录）' : card.can_confirm ? '范围预检完成，等待确认' : '范围预检未完成，禁止确认'}</Badge></div>
        <p className="lead">{card.notice}</p>
        <blockquote className="note note-info"><strong>{card.original_input_label}</strong><div>{card.original_input}</div></blockquote>
        <dl className="kv plan-kv">
          <dt>规范意图</dt><dd>列出单个 LOT 截至历史时刻全部已知且已开始的作业</dd>
          <dt>LOT / 时刻</dt><dd><Code>{card.scope.lot_id}</Code><Code>{card.scope.as_of}</Code>（含时刻边界）</dd>
          <dt>选择与排序</dt><dd>全部可见作业；按进站时间、作业 ID 排序；不按通过/失败筛选</dd>
          <dt>可见性</dt><dd>记录时刻与进站时刻均不晚于查询时刻；尚不可见的记录不作为候选</dd>
          <dt>发现完整性</dt><dd>{card.resolution_receipt.resolution_complete ? '完整' : '不完整'} · 取得 {card.resolution_receipt.fetched_count} / {card.resolution_receipt.total_count ?? '未知'} 个候选</dd>
          <dt>覆盖判定</dt><dd><strong>{card.coverage_receipt.status === 'covered' ? '已覆盖（仅封闭虚构场景）' : card.coverage_receipt.status === 'not_covered' ? '未覆盖' : '覆盖未知'}</strong></dd>
          <dt>覆盖缺口</dt><dd>{card.coverage_receipt.gaps.join('；') || '无'}</dd>
          <dt>发现失败</dt><dd>{card.resolution_receipt.failed_scope.join('；') || '无'}</dd>
          <dt>显式排除</dt><dd>无；不对已知候选另加业务筛选。未来记录不计入本时刻候选。</dd>
          <dt>身份冲突</dt><dd>仅按唯一 LOT 主键解析；不支持通过 SN 解析身份</dd>
          <dt>快照摘要</dt><dd><code className="plan-hash">{card.snapshot_digest}</code></dd>
          <dt>契约束</dt><dd><code className="plan-hash">{card.contract_bundle_hash}</code></dd>
          <dt>确认卡版本</dt><dd>{card.revision} · <code className="plan-hash">{card.card_digest}</code></dd>
        </dl>
        <div className="table-wrap"><table className="table"><thead><tr><th>必需来源</th><th>历史保留起点 (UTC)</th><th>记录覆盖到 (UTC，含边界)</th><th>历史版本保留</th></tr></thead><tbody>{Object.entries(card.coverage_receipt.sources).map(([name, source]) => <tr key={name}><td>{name}</td><td>{source.history_from}</td><td>{source.recorded_through}</td><td>{source.history_retained ? '是' : '否'}</td></tr>)}</tbody></table></div>
        <div className="toolbar">
          <button className="btn" disabled={busy} onClick={() => loadMembers()}>查看完整成员入口</button>
          <button className="btn btn-primary" disabled={busy || !card.can_confirm || !members || !!error || !!plan} onClick={confirm}>确认这张范围卡</button>
          <button className="btn" disabled={busy} onClick={showExport}>读取导出内容</button>
        </div>
        {!members && <div className="small muted">先打开成员入口核对范围，再确认；摘要和散列值不能代替成员列表。</div>}
        {plan && <div className="note note-ok">已保存不可变范围计划 <Code>{plan.confirmed_plan_id}</Code>。此状态不表示产品合格，也不表示分析答案已发布。</div>}
      </section>
      {plan && <section className="panel stack">
        <h3>必需证据执行与调用账本</h3>
        <p className="lead">仅按这张确认计划依次查询 LOT 与全部可见作业。每次尝试最长 5 秒，总预算 15 秒；仅来源忙碌可重试一次。完整执行仍不等于答案发布。</p>
        <div className="toolbar">
          <button className="btn btn-primary" disabled={busy || !!execution} onClick={() => run(() => planApi.execute(plan), applyExecution)}>执行已确认计划</button>
          {execution && <button className="btn" disabled={busy} onClick={() => run(() => planApi.execution(execution.execution_id), applyExecution)}>刷新执行状态</button>}
          {executing && <button className="btn" disabled={busy} onClick={() => run(() => planApi.cancelExecution(execution.execution_id), applyExecution)}>请求取消执行</button>}
          {execution && <button className="btn" disabled={busy} onClick={() => run(() => planApi.executionExport(execution.export_url), result => { setExecution(result.execution); setLedger(result) })}>读取执行账本</button>}
        </div>
        <p className="small muted">执行提交后可点击刷新查看终态。切页或编辑表单只清除本页展示，不取消服务端执行；需要停止时请先请求取消。同一计划重复提交会复用同一执行，不重跑。</p>
        {execution && <>
          <Badge tone={execution.execution_complete ? 'info' : 'warn'} icon={execution.execution_complete ? '◉' : '!'}>{execution.access_state === 'blocked' ? '证据访问已停止' : executionLabels[execution.state]}（证据阶段记录）</Badge>
          <p>{execution.notice}</p>
          <dl className="kv plan-kv">
            <dt>执行 ID</dt><dd><Code>{execution.execution_id}</Code></dd>
            <dt>冻结范围</dt><dd><Code>{execution.scope.lot_id}</Code><Code>{execution.scope.as_of}</Code></dd>
            <dt>实际开始 / 结束 (UTC)</dt><dd>{execution.started_at ?? '尚未开始'} / {execution.finished_at ?? '尚未结束'}</dd>
            <dt>状态持久化</dt><dd>{execution.state_persisted ? '当前状态已保存' : '未保存；不可视为持久完成'}</dd>
            <dt>执行完整性</dt><dd>{execution.execution_complete ? '必需查询全部成功，尚待事实和发布门禁' : '尚未完整完成'}</dd>
            <dt>已完成范围</dt><dd>{execution.completed_scope.join('、') || '无'}</dd>
            <dt>失败范围</dt><dd>{execution.failed_scope.map(s => `${s.source}: ${s.code}`).join('；') || '无'}</dd>
            <dt>未尝试范围</dt><dd>{execution.not_attempted_scope.join('、') || '无'}</dd>
            <dt>执行错误</dt><dd>{execution.error_code ?? '无'}</dd>
          </dl>
          {execution.cancel_requested && executing && <div className="note note-info">取消请求已接收；请刷新核对终态和子进程回收结果。</div>}
          {execution.access_state === 'blocked' && <div className="note note-warn">当前范围前提不再成立：{execution.access_error_code}。只显示诊断账本，证据正文已停止提供。</div>}
          <div className="table-wrap"><table className="table"><thead><tr><th>必需来源</th><th>尝试</th><th>开始 (UTC)</th><th>结束 (UTC)</th><th>状态 / 错误</th><th>子进程回收</th></tr></thead><tbody>{execution.calls.flatMap(call => call.attempts.length ? call.attempts.map(a => <tr key={a.attempt_id}><td>{call.source}</td><td>{a.attempt}</td><td>{a.started_at}</td><td>{a.finished_at ?? '未结束'}</td><td>{a.state}{a.error_code ? ` / ${a.error_code}` : ''}</td><td>{a.cleanup_confirmed === null ? '未知 / 未结束' : a.cleanup_confirmed ? '已确认' : '未确认'}</td></tr>) : [<tr key={call.logical_call_id}><td>{call.source}</td><td colSpan={5}>尚未尝试</td></tr>])}</tbody></table></div>
          {execution.evidence.map(item => <div className="note note-info" key={item.evidence_id}><strong>{item.source}</strong> · 返回 {item.result.returned_rows} / {item.result.total_rows} 条 <Code>{item.evidence_id}</Code><div className="small plan-hash">{item.evidence_digest}</div></div>)}
          {ledger && <div><h3>执行账本导出预览</h3><p>账本列出已取得的结果、实际尝试，以及失败和未尝试范围；没有模型答案。</p><p><a href={execution.export_url}>下载实际执行账本（JSON）</a></p><RawJson data={ledger} title="查看实际执行账本内容" /></div>}
        </>}
      </section>}
      {execution?.execution_complete && execution.access_state === 'available' && <FactsPanel key={execution.execution_id} executionId={execution.execution_id} />}
      {members && <section className="panel stack">
        <h3>绑定版本的成员列表</h3>
        <p className="lead">请求范围候选 {members.requested_scope_count} 个，已发现列表 {members.total} 个，当前页 {members.items.length} 个。{members.resolution_complete ? '发现已完整；翻页只改变展示，不改变确认范围。' : '发现不完整；当前列表不代表请求全集。'}</p>
        <div className="small muted">集合版本 <code className="plan-hash">{members.set_version}</code></div>
        <div className="table-wrap"><table className="table"><thead><tr><th>作业 ID</th><th>LOT</th><th>关卡</th><th>设备</th><th>进站 (UTC)</th><th>出站 (UTC)</th></tr></thead><tbody>{members.items.map(row => <tr key={row.operation_id}><td>{row.operation_id}</td><td>{row.lot_id}</td><td>{row.process_step}</td><td>{row.equipment_id}</td><td>{row.track_in_at}</td><td>{row.track_out_at ?? '本时刻未知'}</td></tr>)}</tbody></table></div>
        <div className="toolbar"><button className="btn" disabled={busy || members.offset === 0} onClick={() => loadMembers(Math.max(0, members.offset - 10))}>上一页</button><button className="btn" disabled={busy || members.next_offset === null} onClick={() => loadMembers(members.next_offset!)}>下一页</button></div>
      </section>}
      {artifact && <section className="panel"><h3>导出预览：查询计划预检</h3><p className="lead">导出包含本次已发现的成员、范围收据与契约束清单。{card.resolution_receipt.resolution_complete ? '发现已完整。' : '发现不完整，已发现成员不代表请求全集。'}没有分析答案。</p><p><a href={plan?.export_url ?? card.export_url}>下载同一导出文件（JSON）</a></p><RawJson data={artifact} title="查看实际导出内容" /></section>}
    </>}
  </div>
}
