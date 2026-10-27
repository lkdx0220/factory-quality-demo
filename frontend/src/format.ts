// Display helpers. Times stay in UTC everywhere — the backend contract is UTC
// and converting to the browser's zone would make "as of" comparisons confusing.

export const UTC_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/

/** Sample data spans 2026-09-01 … 2026-09-10; defaults point into that window. */
export const SAMPLE_TIMES = [
  { value: '2026-09-02T00:00:00Z', label: '09-02 00:00' },
  { value: '2026-09-04T00:00:00Z', label: '09-04 00:00' },
  { value: '2026-09-07T00:00:00Z', label: '09-07 00:00' },
  { value: '2026-09-11T00:00:00Z', label: '09-11 00:00（样例末尾）' },
]

export function utc(value: string | null | undefined): string {
  if (!value) return '—'
  // Backend timestamps are canonical "YYYY-MM-DDTHH:MM:SSZ"; evaluated_at is ISO with offset.
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toISOString().slice(0, 19).replace('T', ' ') + ' UTC'
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '无数据'
  return (value * 100).toFixed(digits) + '%'
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  const hours = seconds / 3600
  if (hours < 48) return `${hours.toFixed(1)} 小时`
  return `${(hours / 24).toFixed(1)} 天`
}
