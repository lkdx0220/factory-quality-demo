// Chinese display text for backend codes. These are translations of codes the
// backend already produced — no decision logic lives here. Unknown codes fall
// back to the raw code so nothing is silently hidden.

import type { Decision } from './api'

export const DECISION: Record<Decision, { label: string; short: string; icon: string; tone: string; meaning: string }> = {
  ALLOW: { label: '满足模拟放行条件', short: '可放行', icon: '✓', tone: 'ok', meaning: '所有必需测试在检查时刻均有有效通过结果，编号绑定一致。' },
  HOLD: { label: '存在确认失败', short: '扣留', icon: '✕', tone: 'bad', meaning: '至少一项必需测试采纳的结果为失败。' },
  REVIEW_BLOCKED: { label: '证据或身份不足，待核查', short: '待核查', icon: '?', tone: 'warn', meaning: '编号绑定、测试规则或测试证据不完整，无法给出放行或扣留结论。' },
}

const REASON: Record<string, string> = {
  ALL_REQUIRED_PASS: '所有必需测试均为有效通过',
  SN_MISSING: '检查时刻未见该单件的 SN 分配记录',
  SN_CONFLICT: 'SN 绑定冲突：同一 SN 对应多个单件，或同一单件对应多个 SN',
  SN_MISMATCH: '请求的 SN 与该单件已分配的 SN 不一致',
  RULE_INCOMPLETE: '检查时刻没有生效的测试要求',
  RULE_OVERLAP: '检查时刻存在多条同时生效的测试要求',
  MISSING_RESULT: '必需测试没有可见结果',
  CONFIRMED_FAIL: '采纳的测试结果为失败',
  INVALID_RESULT: '采纳的测试结果为无效',
  EXPIRED_RESULT: '采纳的测试结果已超过有效期',
}

/** "CONFIRMED_FAIL:TEST_B" → { text, testType } */
export function reasonText(code: string): { text: string; testType: string | null } {
  const [kind, testType] = code.split(':')
  return { text: REASON[kind] ?? code, testType: testType ?? null }
}

export const TEST_STATUS: Record<string, { label: string; tone: string; icon: string }> = {
  PASS: { label: '通过', tone: 'ok', icon: '✓' },
  FAIL: { label: '失败', tone: 'bad', icon: '✕' },
  INVALID: { label: '无效', tone: 'warn', icon: '!' },
  MISSING: { label: '无结果', tone: 'warn', icon: '∅' },
  NOT_REQUIRED: { label: '不要求', tone: 'muted', icon: '–' },
  RULE_INCOMPLETE: { label: '无生效规则', tone: 'warn', icon: '?' },
  RULE_OVERLAP: { label: '规则重叠', tone: 'warn', icon: '?' },
}

export const SN_STATUS: Record<string, { label: string; tone: string }> = {
  OK: { label: '一致', tone: 'ok' },
  MISSING: { label: '未分配', tone: 'warn' },
  CONFLICT: { label: '绑定冲突', tone: 'warn' },
  MISMATCH: { label: '编号不符', tone: 'warn' },
}

export const ALARM_SCOPE: Record<string, string> = {
  UNIT: '单件',
  BINDING: '载具位置',
  OPERATION: '作业',
  EQUIPMENT: '设备',
}

const ERROR: Record<string, string> = {
  NOT_INGESTED: '演示数据库尚未初始化',
  INVALID_DATABASE: '数据库文件不完整或不是本演示的数据库',
  INVALID_BATCH: '配置的演示数据未通过完整性校验',
  CONFLICTING_RETRY: '配置的演示数据未通过完整性校验',
  SNAPSHOT_CONFLICT: '数据库已绑定其他输入快照，请使用新的数据库文件',
  DECISION_CONFLICT: '已存在与本次计算不一致的判定事件，系统拒绝覆盖',
  AMBIGUOUS_SN: 'SN 对应多个单件，需要人工选择',
  INVALID_ARGUMENT: '参数不符合要求',
  CHECK_NOT_FOUND: '找不到该检查请求',
  UNIT_NOT_FOUND: '找不到该单件编号',
  UNIT_NOT_VISIBLE: '查询时刻该单件尚未创建',
  SN_NOT_VISIBLE: '查询时刻没有该 SN 的分配记录',
  LOT_NOT_VISIBLE: '找不到该批次，或查询时刻批次尚未创建',
  OPERATION_NOT_FOUND: '找不到该作业',
  ANCHOR_NOT_VISIBLE: '查询时刻锚点作业尚未完成或尚未记录',
  EQUIPMENT_NOT_FOUND: '找不到该设备',
  STORAGE_ERROR: '数据库暂不可用',
  IO_ERROR: '演示存储不可用',
  NOT_FOUND: '接口或对象不存在',
  INVALID_HOST: '请求地址不被后端接受',
  ORIGIN_NOT_ALLOWED: '后端拒绝了跨源请求',
  INTERNAL_ERROR: '后端内部错误',
  BACKEND_UNREACHABLE: '无法连接后端服务',
  NETWORK_ERROR: '网络请求失败，无法连接前端开发服务器或后端',
  MODEL_DISABLED: '模型规划未启用，可继续使用表单草稿',
  MODEL_BUSY: '已有模型请求进行中，本次未发送',
  MODEL_CONFIGURATION_ERROR: '模型后端配置无效',
  MODEL_CONFIGURATION_CHANGED: '模型配置已改变，请重新理解问题',
  MODEL_AUTH_ERROR: '模型服务认证失败，请检查后端密钥配置',
  MODEL_RATE_LIMIT: '模型服务限流，本次未生成草稿',
  MODEL_UPSTREAM_ERROR: '模型服务拒绝请求，本次未生成草稿',
  MODEL_TIMEOUT: '模型调用超时，本次未生成草稿',
  MODEL_NETWORK_ERROR: '模型服务暂不可达',
  MODEL_RESPONSE_LIMIT: '模型响应超出预算，未采用草稿',
  MODEL_PROTOCOL_ERROR: '模型响应未通过草稿协议，未生成范围计划',
  MODEL_SCOPE_MISMATCH: '模型改变了原文中的 LOT 或截止时刻，草稿已拒绝',
}

export const errorTitle = (code: string) => ERROR[code] ?? '请求失败'
