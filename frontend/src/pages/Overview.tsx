import { api, type Decision } from '../api'
import { Code, ErrorBox, InitButton, Loading, RawJson, Stat, useAsync } from '../components/common'
import { DecisionBadge } from './Packaging'
import { DECISION } from '../labels'
import { utc } from '../format'

export function OverviewPage() {
  const health = useAsync(() => api.health(), [])
  const stats = useAsync(() => api.stats(), [])
  const lots = useAsync(() => api.lots(), [])
  const decisions = useAsync(() => api.decisions(), [])

  const counts = (decisions.data ?? []).reduce<Record<string, number>>((acc, d) => ({ ...acc, [d.decision]: (acc[d.decision] ?? 0) + 1 }), {})

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>数据概览</h1>
          <p className="lead">本地演示数据库的导入状态与基础计数。页面不会自动导入数据。</p>
        </div>
      </div>

      <section className="panel">
        <h3>后端与数据库</h3>
        {health.loading && <Loading />}
        {health.error && <ErrorBox error={health.error} onRetry={health.retry} />}
        {health.data && (
          <dl className="kv">
            <dt>后端服务</dt><dd>运行中 · API 版本 <Code>{health.data.version}</Code></dd>
            <dt>数据库文件</dt><dd>{health.data.database_exists ? '已存在' : '不存在（尚未初始化）'}</dd>
          </dl>
        )}
        {health.data && !health.data.database_exists && <InitButton />}
      </section>

      {stats.loading && !stats.data && <Loading />}
      {stats.error && !health.error && <ErrorBox error={stats.error} onRetry={stats.retry} />}
      {stats.data && (
        <>
          <div className="stat-grid">
            <Stat label="批次" value={stats.data.lots} />
            <Stat label="单件" value={stats.data.units} />
            <Stat label="测试尝试" value={stats.data.test_attempts} sub={`记录版本 ${stats.data.test_record_versions}（含更正）`} />
            <Stat label="结果投递" value={stats.data.delivery_receipts} sub={`其中重复 ${stats.data.duplicate_deliveries} 次，已去重`} />
            <Stat label="已保存判定事件" value={stats.data.packaging_decisions} />
          </div>
          {decisions.data && decisions.data.length > 0 && (
            <section className="panel">
              <h3>已保存判定分布</h3>
              <div className="legend">
                {(Object.keys(DECISION) as Decision[]).map((k) => (
                  <div key={k} className="legend-item"><DecisionBadge decision={k} /><strong>{counts[k] ?? 0}</strong> 条</div>
                ))}
              </div>
            </section>
          )}
          <section className="panel">
            <h3>导入来源</h3>
            <dl className="kv">
              <dt>快照</dt><dd><Code>{stats.data.source.batch_id}</Code></dd>
              <dt>导入时间</dt><dd>{utc(stats.data.source.imported_at)}</dd>
              <dt>数据摘要</dt><dd><Code>{stats.data.source.data_digest.slice(0, 16)}…</Code></dd>
            </dl>
            <RawJson data={stats.data} />
          </section>
        </>
      )}

      {lots.data && lots.data.length > 0 && (
        <section className="panel">
          <h3>批次</h3>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>批次</th><th>产品</th><th>投入数量</th><th>创建时间 (UTC)</th></tr></thead>
              <tbody>
                {lots.data.map((l) => (
                  <tr key={l.lot_id}><td>{l.lot_id}</td><td>{l.product_id}</td><td>{l.initial_quantity}</td><td>{utc(l.created_at)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}
