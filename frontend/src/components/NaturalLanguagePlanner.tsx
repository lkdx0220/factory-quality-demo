import { useState } from 'react'
import type { ApiError } from '../api'
import type { PlanCard } from '../planApi'
import { plannerApi, type ModelProposal } from '../plannerApi'
import { Badge, Code, ErrorBox, toApiError, useAsync, useRequestGuard } from './common'

export function NaturalLanguagePlanner({ onReset, onCard }: { onReset: () => void; onCard: (card: PlanCard) => void }) {
  const config = useAsync(plannerApi.config, [])
  const [question, setQuestion] = useState('截至 2026-09-07T00:00:00Z，列出 LOT-0001 的全部可见作业。')
  const [proposal, setProposal] = useState<ModelProposal>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError>()
  const [adopted, setAdopted] = useState(false)
  const guard = useRequestGuard()

  function edit(value: string) {
    guard.invalidate()
    setQuestion(value); setProposal(undefined); setError(undefined); setBusy(false); setAdopted(false)
    onReset()
  }

  async function run<T,>(work: () => Promise<T>, apply: (result: T) => void) {
    const token = guard.begin()
    onReset(); setBusy(true); setError(undefined); setAdopted(false)
    try {
      const result = await work()
      if (guard.isCurrent(token)) apply(result)
    } catch (e) {
      if (guard.isCurrent(token)) { setError(toApiError(e)); setProposal(undefined) }
    } finally { if (guard.isCurrent(token)) setBusy(false) }
  }

  return <section className="panel stack" aria-label="自然语言草稿">
    <h2>先理解问题，再核对范围</h2>
    <p className="lead">只支持单个 LOT 的全部可见作业清单。请写明 LOT 编号和 UTC 截止时刻；模型不会计算结果，也不会替你确认。</p>
    <p className="small muted">当前只识别有限中英文完整句式，例如“截至 UTC，列出 LOT 的全部可见作业”或“List all operations for LOT as of UTC.”。排除、质量要求及未识别的附加内容不会自动删去；请按原意重新表述，不能为通过检查而省略要求。</p>
    {config.loading && <p role="status">正在读取模型配置…</p>}
    {config.error && <ErrorBox error={config.error} onRetry={config.retry} />}
    {config.data && <>
      <p><Badge tone={config.data.enabled ? 'info' : 'warn'} icon={config.data.enabled ? '◉' : '!'}>{config.data.enabled ? '模型草稿已启用' : '模型草稿未启用'}</Badge> <Code>{config.data.model}</Code></p>
      <div className="note note-info">{config.data.notice} {config.data.enabled ? '点击理解问题会将本次原话发送到 DeepSeek，按服务商规则计费；不会发送业务表、查询结果或历史会话。' : '默认关闭；可继续使用表单草稿。密钥由后端环境变量配置，不在网页输入。'}</div>
    </>}
    <label className="mode-field">自然语言问题（仅作未经背书的原话）
      <textarea className="input" aria-label="自然语言问题" rows={3} maxLength={2000} value={question} onChange={e => edit(e.target.value)} />
    </label>
    <div className="toolbar"><button className="btn btn-primary" disabled={busy || !config.data?.enabled || !question.trim()} onClick={() => { setProposal(undefined); void run(() => plannerApi.propose(question), setProposal) }}>{busy ? '正在处理草稿…' : '理解问题'}</button></div>
    {busy && <p role="status">等待服务端处理；不显示原始模型流。编辑问题会丢弃晚到结果，不代表撤销已经发出的模型调用。</p>}
    {error && <ErrorBox error={error} />}
    {proposal && <div className="stack" aria-label="服务端草稿结果">
      <Badge tone={proposal.status === 'ready' ? 'info' : 'warn'} icon={proposal.status === 'ready' ? '◉' : '?'}>{proposal.status === 'ready' ? '待核对草稿，尚未确认范围' : proposal.status === 'needs_clarification' ? '请补充阻塞信息' : '当前入口不支持原问题'}</Badge>
      <p>{proposal.notice}</p>
      <blockquote className="note note-info"><strong>{proposal.original_input_label}</strong><div>{proposal.original_input}</div></blockquote>
      {proposal.questions.length > 0 && <ul>{proposal.questions.map(q => <li key={q}>{q}</li>)}</ul>}
      {proposal.reason_codes.length > 0 && <p className="small muted">服务端原因：{proposal.reason_codes.join('、')}</p>}
      {proposal.normalized_scope && <>
        <dl className="kv"><dt>草稿意图</dt><dd>单个 LOT 的全部可见作业清单；不判断质量</dd><dt>LOT</dt><dd><Code>{proposal.normalized_scope.lot_id}</Code></dd><dt>历史截止（UTC，含边界）</dt><dd><Code>{proposal.normalized_scope.as_of}</Code></dd><dt>范围</dt><dd>当时已知且已经开始的全部作业，不筛选 PASS/FAIL</dd></dl>
        <p>请先判断草稿是否对应你的原问题。下一步仍需核对服务端发现的成员、覆盖情况，并显式确认范围卡。</p>
        <button className="btn btn-primary" disabled={busy || adopted} onClick={() => void run(() => plannerApi.preflight(proposal), card => { onCard(card); setAdopted(true) })}>按此草稿生成范围确认卡</button>
      </>}
      {proposal.model_call && <p className="small muted">本次模型 {proposal.model_call.model} · 提示词 {proposal.model_call.prompt_version} · 用量 {proposal.model_call.usage.total_tokens} tokens。模型草稿不是业务答案。</p>}
      {!proposal.model_called && <p className="small muted">本次由服务端能力或缺项检查处理，未发出模型请求。</p>}
    </div>}
  </section>
}
