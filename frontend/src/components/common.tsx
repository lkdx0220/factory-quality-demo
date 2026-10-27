import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { ApiError, api } from '../api'
import { errorTitle } from '../labels'
import { SAMPLE_TIMES, UTC_PATTERN } from '../format'
import { createRequestGuard } from '../requestGuard'

/** App-wide signal: bump `version` after initialization so pages reload their data. */
export const DataContext = createContext<{ version: number; reload: () => void }>({ version: 0, reload: () => {} })

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const { version } = useContext(DataContext)
  const [state, setState] = useState<{ data?: T; error?: ApiError; loading: boolean }>({ loading: true })
  const run = useCallback(() => {
    let alive = true
    setState((s) => ({ ...s, loading: true, error: undefined }))
    fn().then(
      (data) => alive && setState({ data, loading: false }),
      (error) => alive && setState({ error: toApiError(error), loading: false }),
    )
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, version])
  useEffect(run, [run])
  return { ...state, retry: run }
}

/** One guard per component; unmounting makes every in-flight request stale. */
export function useRequestGuard() {
  const guard = useRef(createRequestGuard()).current
  useEffect(() => () => guard.invalidate(), [guard])
  return guard
}

export function toApiError(error: unknown): ApiError {
  return error instanceof ApiError ? error : new ApiError('backend', null, 'CLIENT_ERROR', String(error))
}

export function Badge({ tone, icon, children, title }: { tone: string; icon?: string; children: ReactNode; title?: string }) {
  return (
    <span className={`badge tone-${tone}`} title={title}>
      {icon && <span className="badge-icon" aria-hidden>{icon}</span>}
      {children}
    </span>
  )
}

export function Code({ children }: { children: ReactNode }) {
  return <code className="code">{children}</code>
}

export function Loading({ text = '正在读取…' }: { text?: string }) {
  return <div className="state state-loading"><span className="spinner" aria-hidden />{text}</div>
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state state-empty">
      <strong>{title}</strong>
      {children && <div className="state-hint">{children}</div>}
    </div>
  )
}

export function InitButton({ onDone }: { onDone?: () => void }) {
  const { reload } = useContext(DataContext)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError>()
  const [result, setResult] = useState<string>()
  async function run() {
    setBusy(true)
    setError(undefined)
    try {
      const r = await api.ingest()
      setResult(r.reused ? '数据库已存在同一快照，未重复导入。' : `已导入快照 ${r.batch_id}：${r.test_attempts} 次测试尝试，${r.delivery_receipts} 次投递（其中重复 ${r.duplicate_deliveries} 次）。`)
      reload()
      onDone?.()
    } catch (e) {
      setError(toApiError(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="init-box">
      <button className="btn btn-primary" onClick={run} disabled={busy}>{busy ? '正在初始化…' : '初始化演示数据库'}</button>
      <span className="muted small">导入服务端配置的合成数据快照（POST /api/v1/ingest）。重复执行会复用已有导入。</span>
      {result && <div className="note note-ok">{result}</div>}
      {error && <ErrorBox error={error} />}
    </div>
  )
}

export function ErrorBox({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const unreachable = error.kind === 'unreachable'
  return (
    <div className={`error-box ${unreachable ? 'error-offline' : ''}`} role="alert">
      <div className="error-head">
        <span className="error-icon" aria-hidden>{unreachable ? '⚡' : '!'}</span>
        <strong>{errorTitle(error.code)}</strong>
        <span className="muted small">
          {error.status !== null && <>HTTP {error.status} · </>}<Code>{error.code}</Code>
        </span>
      </div>
      <div className="error-message">{error.message}</div>
      {unreachable && (
        <div className="small">请确认后端已在项目根目录启动：<Code>python scripts/serve_api.py</Code>（默认 127.0.0.1:8000）。页面不会用模拟数据替代。</div>
      )}
      {error.details !== undefined && error.details !== null && (
        <details className="raw small"><summary>错误详情（原始）</summary><pre>{JSON.stringify(error.details, null, 2)}</pre></details>
      )}
      {error.code === 'NOT_INGESTED' && <InitButton onDone={onRetry} />}
      {onRetry && <button className="btn btn-ghost" onClick={onRetry}>重试</button>}
    </div>
  )
}

export function RawJson({ data, title = '原始 JSON（接口返回）' }: { data: unknown; title?: string }) {
  return (
    <details className="raw">
      <summary>{title}</summary>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </details>
  )
}

/** Explicit UTC instant input: date + time fields composed to the canonical contract format. */
export function UtcInput({ label, value, onChange, presets = SAMPLE_TIMES }: {
  label: string
  value: string
  onChange: (value: string) => void
  presets?: { value: string; label: string }[]
}) {
  const valid = UTC_PATTERN.test(value)
  const date = valid ? value.slice(0, 10) : ''
  const time = valid ? value.slice(11, 19) : ''
  const compose = (d: string, t: string) => {
    const tt = t.length === 5 ? t + ':00' : t
    onChange(d && tt ? `${d}T${tt}Z` : '')
  }
  return (
    <fieldset className="utc-input">
      <legend>{label} <span className="utc-tag">UTC</span></legend>
      <div className="utc-row">
        <input type="date" aria-label={label + ' 日期（UTC）'} value={date} onChange={(e) => compose(e.target.value, time || '00:00:00')} />
        <input type="time" step={1} aria-label={label + ' 时间（UTC）'} value={time} onChange={(e) => compose(date, e.target.value)} />
      </div>
      <div className="utc-presets">
        {presets.map((p) => (
          <button type="button" key={p.value} className={`chip ${p.value === value ? 'chip-on' : ''}`} onClick={() => onChange(p.value)}>{p.label}</button>
        ))}
      </div>
      <div className="small muted">提交值：<Code>{value || '（未填写）'}</Code></div>
    </fieldset>
  )
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div className={`stat ${tone ? 'tone-' + tone : ''}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}
