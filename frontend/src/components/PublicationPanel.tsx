import { useEffect, useState } from 'react'
import { publicationApi, type DisplayOperation, type PublicationView, type PublicationPage, type PublicationArtifact } from '../publicationApi'
import { Code, RawJson, toApiError, useRequestGuard } from './common'

function Rows({ rows }: { rows: DisplayOperation[] }) {
  if (!rows.length) return <p className="note note-info">当前完整范围内没有可见作业。这是成功的零行结果，不表示缺少证据或产品合格。</p>
  return <div className="table-wrap"><table className="table"><thead><tr>{rows[0].cells.map(cell => <th key={cell.key}>{cell.label}</th>)}</tr></thead>
    <tbody>{rows.map(row => <tr key={row.operation_id}>{row.cells.map(cell => <td key={cell.fact_id}><span className="plan-hash">{cell.display_value}</span></td>)}</tr>)}</tbody></table></div>
}

export function PublicationPanel({ factSetId, digest, validationId }: { factSetId: string; digest: string; validationId: string }) {
  const [view, setView] = useState<PublicationView>()
  const [page, setPage] = useState<PublicationPage>()
  const [artifact, setArtifact] = useState<PublicationArtifact>()
  const [errorCode, setErrorCode] = useState<string>()
  const [busy, setBusy] = useState(false)
  const guard = useRequestGuard()
  // Eligibility can expire without user interaction. Do not keep an expired answer visible.
  useEffect(() => {
    if (!view) return
    const timer = window.setTimeout(() => {
      guard.invalidate(); setBusy(false); setView(undefined); setPage(undefined); setArtifact(undefined); setErrorCode('PLAN_EXPIRED')
    }, Math.max(0, Math.min(2147483647, view.expires_at_epoch * 1000 - Date.now())))
    return () => window.clearTimeout(timer)
  }, [view, guard])

  async function run<T,>(work: () => Promise<T>, apply: (result: T) => void) {
    const token = guard.begin()
    setBusy(true); setErrorCode(undefined)
    try {
      const result = await work()
      if (guard.isCurrent(token)) apply(result)
    } catch (e) {
      if (guard.isCurrent(token)) {
        const code = toApiError(e).code
        setErrorCode(/^[A-Z][A-Z0-9_]{0,79}$/.test(code) ? code : 'OUTPUT_UNAVAILABLE')
        setView(undefined); setPage(undefined); setArtifact(undefined)
      }
    } finally { if (guard.isCurrent(token)) setBusy(false) }
  }
  function applyView(result: PublicationView) { setView(result); setPage(undefined); setArtifact(undefined) }
  function members(offset = 0) { if (view) return run(() => publicationApi.members(view.details_url, offset), setPage) }

  return <section className="panel stack" aria-label="正式答案发布">
    <h3>正式答案与最终发布门禁</h3>
    <p>只使用已确认范围及通过校验的全部事实；不使用模型自由文字。</p>
    <div className="toolbar">
      <button className="btn btn-primary" disabled={busy} onClick={() => run(() => publicationApi.publish(factSetId, digest, validationId), applyView)}>生成正式作业清单答案</button>
      {view && <>
        <button className="btn" disabled={busy} onClick={() => run(() => publicationApi.read(view.publication_id), applyView)}>重新核验答案资格</button>
        <button className="btn" disabled={busy} onClick={() => members()}>查看完整作业明细</button>
        <button className="btn" disabled={busy} onClick={() => run(() => publicationApi.export(view.export_url), result => { applyView(result.view); setArtifact(result) })}>读取正式答案导出</button>
      </>}
    </div>
    {busy && <p role="status">正在重新核验范围、证据、必需事实与固定输出；完成前不显示候选内容。</p>}
    {errorCode && <div className="note note-warn" role="alert"><strong>答案不可用；本次未返回正式答案</strong><p>发布或访问校验未完成。请检查当前范围与服务状态，必要时重新生成并确认计划。</p><Code>{errorCode}</Code></div>}
    {view && <>
      <h3>{view.title}</h3><div className="note note-ok"><strong>{view.status_label}</strong><p>{view.notice}</p></div>
      <dl className="kv plan-kv">{view.summary.map(fact => <div style={{ display: 'contents' }} key={fact.fact_id}><dt>{fact.label}</dt><dd><span className="plan-hash">{fact.display_value}</span>{fact.origin !== 'registered_derivation' && <span className="small muted"> · {fact.origin === 'confirmed_scope' ? '已确认范围参数' : '源数据值'}</span>}</dd></div>)}</dl>
      <div className="note note-info">{view.source_notice}<br />{view.time_notice}<br />{view.order_notice}</div>
      <p>执行完整性：完整。业务质量证据：此清单意图不适用。必需事实 {view.completeness.required_facts} 项 / 已纳入 {view.completeness.rendered_facts} 项；作业总数 {view.total_rows} 条。</p>
      <p>发布时间（UTC）：<Code>{view.published_at}</Code>；该时间不代表业务历史截止。当前访问核验通过；后续读取、分页和下载均重新核验，已送达内容无法追溯撤回。</p>
      <h4>作业预览：展示 {view.preview_rows} / 全部 {view.total_rows} 条</h4><Rows rows={view.preview} />
      {page && <div className="stack"><h4>完整作业明细入口</h4><p>当前页 {page.returned_rows} 条 / 全部 {page.total} 条；起始位置 {page.offset}。</p><Rows rows={page.items} />
        <div className="toolbar"><button className="btn" disabled={busy || page.offset === 0} onClick={() => members(Math.max(0, page.offset - 3))}>作业上一页</button><button className="btn" disabled={busy || page.next_offset === null} onClick={() => members(page.next_offset!)}>作业下一页</button></div></div>}
      {artifact && <div><h4>正式答案实际导出</h4><p>完整答案、全部作业、事实来源链及绑定校验收据；下载时再次核验。</p><a href={view.export_url}>下载正式答案与完整明细（JSON）</a><RawJson data={artifact} title="查看正式答案导出内容" /></div>}
    </>}
  </section>
}
