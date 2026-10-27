import { useState } from 'react'
import type { ApiError } from '../api'
import { factsApi, type FactPage, type FactValidation, type FactView, type RegisteredFact } from '../factsApi'
import { Badge, Code, ErrorBox, RawJson, toApiError, useRequestGuard } from './common'
import { PublicationPanel } from './PublicationPanel'

const labels: Record<string, string> = {
  lot_id: 'LOT 身份', as_of: '历史截止 (UTC)', operation_count: '全部可见作业数（作业记录）',
  member_set_digest: '完整成员摘要', membership_complete: '成员范围完整', exclusion_count: '显式排除数',
  metric_applicability: '业务指标适用性',
}

function scalar(fact: RegisteredFact) {
  if (fact.fact_key === 'metric_applicability') return '此意图不判定质量；应测、通过、失败等指标不适用'
  if (fact.value === null) return '本时刻未知（null）'
  if (typeof fact.value === 'boolean') return fact.value ? '是' : '否'
  return String(fact.value)
}

export function FactsPanel({ executionId }: { executionId: string }) {
  const [view, setView] = useState<FactView>()
  const [page, setPage] = useState<FactPage>()
  const [receipt, setReceipt] = useState<FactValidation | null>()
  const [artifact, setArtifact] = useState<Record<string, unknown>>()
  const [error, setError] = useState<ApiError>()
  const [busy, setBusy] = useState(false)
  const guard = useRequestGuard()

  async function run<T,>(work: () => Promise<T>, apply: (result: T) => void) {
    const token = guard.begin()
    setBusy(true); setError(undefined)
    try {
      const result = await work()
      if (guard.isCurrent(token)) apply(result)
    } catch (e) {
      if (guard.isCurrent(token)) {
        setError(toApiError(e)); setView(undefined); setPage(undefined); setReceipt(undefined); setArtifact(undefined)
      }
    } finally { if (guard.isCurrent(token)) setBusy(false) }
  }

  function applyView(result: FactView) {
    setView(result); setReceipt(result.latest_validation); setPage(undefined); setArtifact(undefined)
  }

  function members(offset = 0) {
    if (view) return run(() => factsApi.members(view.members_url, offset), setPage)
  }

  return <section className="panel stack" aria-label="事实注册与引用校验">
    <h3>事实注册与必需事实校验</h3>
    <p className="lead">重新核对必需证据，再生成绑定当前范围的叶子事实和确定性派生事实。校验通过只表示引用与必需事实齐全，尚未完成最终答案发布。</p>
    <div className="toolbar">
      <button className="btn btn-primary" disabled={busy} onClick={() => run(() => factsApi.create(executionId), applyView)}>生成必需事实集</button>
      {view && <>
        <button className="btn" disabled={busy} onClick={() => run(() => factsApi.read(view.fact_set_id), applyView)}>刷新事实与资格</button>
        <button className="btn" disabled={busy} onClick={() => members()}>查看全部事实入口</button>
        <button className="btn" disabled={busy} onClick={() => run(async () => {
          const proposal = await factsApi.proposal(view.fact_set_id)
          return factsApi.validate(view.fact_set_id, proposal)
        }, result => { setReceipt(result); setArtifact(undefined) })}>校验全部必需事实</button>
        <button className="btn" disabled={busy} onClick={() => run(() => factsApi.export(view.export_url), result => {
          applyView(result.view); setArtifact(result)
        })}>读取事实集导出</button>
      </>}
    </div>
    {error && <ErrorBox error={error} />}
    {view && <>
      <Badge tone="info" icon="◉">G2 必需证据已核验（事实阶段记录）</Badge>
      <p>事实集本身不签发答案；最终发布状态仅见下方独立的正式答案区。</p>
      <p>绑定范围 <Code>{view.lot_id}</Code><Code>{view.as_of}</Code> · 必需事实 {view.required_count} / 注册事实 {view.fact_count} 项。这里的数量是事实条目，作业数见下方独立事实。</p>
      <dl className="kv plan-kv">{view.summary_facts.map(fact => <div style={{ display: 'contents' }} key={fact.fact_id}>
        <dt>{labels[fact.fact_key] ?? fact.fact_key}</dt><dd><span className="plan-hash">{scalar(fact)}</span></dd>
      </div>)}</dl>
      <div className="note note-info">执行完整性：必需查询完整。业务证据适用性：本意图仅列作业，质量/测试判定指标不适用。展示范围：摘要为 7 项；全部事实可通过绑定版本的分页入口和导出查看。</div>
      <div className="small muted">事实集 <code className="plan-hash">{view.fact_set_id}</code><br />注册表 {view.registry_version} · 计数域 all_visible_operation_records</div>
      {receipt && <div className={receipt.accepted ? 'note note-ok' : 'note note-warn'}>
        <strong>{receipt.accepted ? 'G3 引用与必需事实校验通过（引用阶段记录）' : 'G3 引用校验拒绝；不能据此发布答案'}</strong>
        <p>提交 {receipt.submitted_count} 项 / 必需 {receipt.required_count} 项；缺失 {receipt.missing_required_fact_ids.length} 项。此状态不表示产品合格。</p>
        {receipt.issues.length > 0 && <p>{receipt.issues.map(issue => issue.code).join('；')}</p>}
      </div>}
      {page && <div className="stack">
        <h4>全部事实明细（绑定同一事实集）</h4>
        <p>当前页 {page.returned_rows} 项 / 全部 {page.total} 项；分页只改变展示，不改变必需事实集合。</p>
        <div className="table-wrap"><table className="table"><thead><tr><th>稳定事实键</th><th>对象</th><th>值</th><th>单位</th><th>来源类型</th></tr></thead><tbody>{page.items.map(fact => <tr key={fact.fact_id}>
          <td>{fact.fact_key}</td><td>{fact.entity_id}</td><td><span className="plan-hash">{scalar(fact)}</span></td><td>{fact.unit}</td><td>{fact.algorithm_id ? '已注册确定性算法' : '源证据叶子值'}</td>
        </tr>)}</tbody></table></div>
        <div className="toolbar"><button className="btn" disabled={busy || page.offset === 0} onClick={() => members(Math.max(0, page.offset - 10))}>事实上一页</button><button className="btn" disabled={busy || page.next_offset === null} onClick={() => members(page.next_offset!)}>事实下一页</button></div>
        <RawJson data={page} title="查看本页事实身份与来源链" />
      </div>}
      {artifact && <div><h4>事实集实际导出</h4><p>包含全部事实、来源链、必需集合、最新校验收据及严格 Schema；没有分析答案。</p><p><a href={view.export_url}>下载事实集与校验收据（JSON）</a></p><RawJson data={artifact} title="查看实际事实集导出内容" /></div>}
    </>}
    {view && receipt?.accepted && <PublicationPanel key={receipt.validation_id} factSetId={view.fact_set_id} digest={view.fact_set_digest} validationId={receipt.validation_id} />}
  </section>
}
