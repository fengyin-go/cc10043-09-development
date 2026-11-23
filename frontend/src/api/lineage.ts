import { request } from './client'

/** 血缘四要素（含起止时间）：一次查询必须全部给出确切值。 */
export type LineageCriteria = {
  来源名称: string
  接入批次: string
  分区: string
  起始时间: string
  结束时间: string
}

export type LineagePayload = {
  消息ID: string
  来源名称: string
  归属状态: string
  归属有效: boolean
  冲突来源: string[]
  存储时间: string
}

export type LineageSegment = {
  分段标识: string
  来源名称: string
  接入批次: string
  分区: string
  起始时间: string
  结束时间: string
  检查点证据: { 检查点值: string; 检查点状态: string; 有效: boolean }
  落地分区证据: { 落地分区: string; 落地证明状态: string; 撤销时间: string; 有效: boolean }
  归属载荷: LineagePayload[]
  证据版本: number
  缺失证据: string[]
  证据齐全: boolean
}

export type LineageQueryResult = {
  ok: boolean
  message: string
  criteria: LineageCriteria
  segments: LineageSegment[]
  segment_total: number
  complete_total: number
  gap_total: number
  ambiguous_payloads: { 消息ID: string; 来源名称: string; 冲突来源: string[]; 说明: string }[]
}

export type ReplayPrecheckResult = {
  ok: boolean
  message: string
  ready_segments: { 分段标识: string; 缺失证据: string[]; 候选消息: string[] }[]
  blocked_segments: { 分段标识: string; 缺失证据: string[]; 候选消息: string[] }[]
  candidate_messages: string[]
  exact_range: LineageCriteria
  ambiguous_payloads: LineageQueryResult['ambiguous_payloads']
}

export type DispatchResult = {
  ok: boolean
  message: string
  status?: string
  delivered: {
    消息ID: string
    来源名称: string
    接入批次: string
    分区: string
    落地分区: string
    证据版本: number
  }[]
  refused: { 分段标识: string; 消息ID?: string; 原因: string }[]
  delivery_range: LineageCriteria
  replay_id?: number
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  if (!response.ok) throw new Error(`接口返回 ${response.status}，血缘操作未执行`)
  return (await response.json()) as T
}

export function queryLineage(values: LineageCriteria): Promise<LineageQueryResult> {
  return post<LineageQueryResult>('/api/lineage/query', { values })
}

export function queryLineageGaps(values: LineageCriteria): Promise<LineageQueryResult> {
  return post<LineageQueryResult>('/api/lineage/gaps', { values })
}

export function precheckReplay(values: LineageCriteria): Promise<ReplayPrecheckResult> {
  return post<ReplayPrecheckResult>('/api/lineage/replay/precheck', { values })
}

export function dispatchReplay(values: LineageCriteria): Promise<DispatchResult> {
  return post<DispatchResult>('/api/lineage/replay/dispatch', { values })
}

export function revokeEvidence(values: Pick<LineageCriteria, '来源名称' | '接入批次' | '分区'>) {
  return post<{ ok: boolean; message: string; segment?: string }>('/api/lineage/evidence/revoke', { values })
}

export function supplementEvidence(
  values: Pick<LineageCriteria, '来源名称' | '接入批次' | '分区'>,
  sinkPartition: string,
) {
  return post<{ ok: boolean; message: string; segment?: string }>('/api/lineage/evidence/supplement', {
    values,
    sink_partition: sinkPartition,
  })
}
