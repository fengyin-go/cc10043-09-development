<template>
  <section class="lineage-panel" data-testid="lineage-panel">
    <header class="lineage-head">
      <h3>血缘查询与回放演练</h3>
      <p class="page-desc">
        一次查询必须给出确切来源、批次、分区和时间窗口；orders-us 与 orders-eu
        互相不能补全对方的血缘，缺证分段和歧义载荷会原样保留。
      </p>
    </header>

    <form class="analysis-form" @submit.prevent="runQuery">
      <label v-for="field in criteriaFields" :key="field">
        <span>{{ field }}</span>
        <input v-model="criteria[field]" :data-testid="`lineage-filter-${field}`" :placeholder="field" />
      </label>
      <button class="btn primary" data-testid="lineage-query" type="submit">查询血缘</button>
      <button class="btn" data-testid="lineage-gaps" type="button" @click="runGapQuery">只看缺口</button>
      <button class="btn ghost" type="button" @click="resetCriteria">重置条件</button>
    </form>

    <p v-if="feedback" class="lineage-feedback" :class="feedbackKind" data-testid="lineage-feedback">
      {{ feedback }}
    </p>

    <div v-if="queryResult" class="analysis-summary">
      <article class="analysis-card">
        <span class="stat-label">命中断段</span>
        <strong class="stat-value">{{ queryResult.segment_total }}</strong>
      </article>
      <article class="analysis-card">
        <span class="stat-label">证据齐全</span>
        <strong class="stat-value">{{ queryResult.complete_total }}</strong>
      </article>
      <article class="analysis-card">
        <span class="stat-label">缺口分段</span>
        <strong class="stat-value">{{ queryResult.gap_total }}</strong>
      </article>
      <article class="analysis-card">
        <span class="stat-label">歧义载荷</span>
        <strong class="stat-value">{{ queryResult.ambiguous_payloads.length }}</strong>
      </article>
    </div>

    <table v-if="queryResult" class="data-table lineage-table">
      <thead>
        <tr>
          <th>分段标识</th>
          <th>检查点证据</th>
          <th>落地分区证据</th>
          <th>归属载荷</th>
          <th>证据版本</th>
          <th>证据状态</th>
          <th>演练操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="segment in queryResult.segments" :key="segment.分段标识" :data-testid="`segment-${segment.分区}`">
          <td>
            <strong>{{ segment.分段标识 }}</strong>
            <br />
            <small>{{ segment.起始时间 }} ~ {{ segment.结束时间 }}</small>
          </td>
          <td>
            <span :class="segment.检查点证据.有效 ? 'evidence-ok' : 'evidence-bad'">
              {{ segment.检查点证据.有效 ? '✓' : '✗' }}
            </span>
            {{ segment.检查点证据.检查点值 }}
          </td>
          <td>
            <span :class="segment.落地分区证据.有效 ? 'evidence-ok' : 'evidence-bad'">
              {{ segment.落地分区证据.有效 ? '✓' : '✗' }}
            </span>
            {{ segment.落地分区证据.落地分区 || '—' }}
            <br />
            <small>状态：{{ segment.落地分区证据.落地证明状态 }}</small>
          </td>
          <td>
            <div v-for="payload in segment.归属载荷" :key="payload.消息ID" class="payload-line">
              <span :class="payload.归属有效 ? 'evidence-ok' : 'evidence-bad'">
                {{ payload.归属状态 === '唯一' ? '✓' : '⚠' }}
              </span>
              {{ payload.消息ID }}（{{ payload.归属状态 }}）
            </div>
            <em v-if="!segment.归属载荷.length">无归属载荷</em>
          </td>
          <td>v{{ segment.证据版本 }}</td>
          <td>
            <span :class="segment.证据齐全 ? 'status-ok' : 'status-bad'">
              {{ segment.证据齐全 ? '证据齐全' : `缺：${segment.缺失证据.join('、')}` }}
            </span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="runPrecheck(segment)">回放预检</button>
            <button class="link" type="button" @click="runDispatch(segment)">派发回放</button>
            <button class="link danger" type="button" @click="runRevoke(segment)">撤销落地证明</button>
            <button class="link" type="button" @click="openSupplement(segment)">补证</button>
          </td>
        </tr>
        <tr v-if="!queryResult.segments.length">
          <td colspan="7" class="empty-state">
            当前四要素条件下没有分段（不会用其他来源/分区的数据补全）
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="queryResult?.ambiguous_payloads.length" class="ambiguous-box" data-testid="ambiguous-box">
      <strong>归属冲突载荷（禁止作为回放输入）：</strong>
      <ul>
        <li v-for="item in queryResult.ambiguous_payloads" :key="item.消息ID">
          {{ item.消息ID }} — 冲突来源：{{ item.冲突来源.join('、') }}；{{ item.说明 }}
        </li>
      </ul>
    </div>

    <div v-if="precheckResult" class="drill-box" data-testid="precheck-box">
      <h4>回放预检结果</h4>
      <p>{{ precheckResult.message }}</p>
      <p>
        候选消息（{{ precheckResult.candidate_messages.length }} 条）：
        <strong>{{ precheckResult.candidate_messages.join('、') || '无' }}</strong>
      </p>
      <p v-if="precheckResult.blocked_segments.length" class="status-bad">
        拦截分段：{{ precheckResult.blocked_segments.map((item) => item.分段标识).join('、') }}
      </p>
      <p>确切数据范围：{{ formatCriteria(precheckResult.exact_range) }}</p>
    </div>

    <div v-if="dispatchResult" class="drill-box" data-testid="dispatch-box">
      <h4>实际派发结果</h4>
      <p>{{ dispatchResult.message }}（任务状态：{{ dispatchResult.status }}）</p>
      <table v-if="dispatchResult.delivered.length" class="data-table">
        <thead>
          <tr>
            <th>消息ID</th>
            <th>来源</th>
            <th>批次</th>
            <th>分区</th>
            <th>实际落地分区</th>
            <th>证据版本</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in dispatchResult.delivered" :key="item.消息ID">
            <td>{{ item.消息ID }}</td>
            <td>{{ item.来源名称 }}</td>
            <td>{{ item.接入批次 }}</td>
            <td>{{ item.分区 }}</td>
            <td>{{ item.落地分区 }}</td>
            <td>v{{ item.证据版本 }}</td>
          </tr>
        </tbody>
      </table>
      <ul v-if="dispatchResult.refused.length" class="status-bad">
        <li v-for="item in dispatchResult.refused" :key="`${item.分段标识}-${item.消息ID ?? ''}`">
          拦截：{{ item.分段标识 }} {{ item.消息ID ?? '' }} — {{ item.原因 }}
        </li>
      </ul>
      <p>实际交付范围：{{ formatCriteria(dispatchResult.delivery_range) }}</p>
    </div>

    <div v-if="showSupplement" class="modal-backdrop" data-testid="supplement-modal">
      <form class="modal" @submit.prevent="submitSupplement">
        <h3>补齐落地分区证明</h3>
        <p class="page-desc">{{ supplementTarget?.分段标识 }}</p>
        <label class="create-field">
          <span>确切落地分区</span>
          <input v-model="supplementPartition" data-testid="supplement-partition" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showSupplement = false">取消</button>
          <button class="btn primary" data-testid="supplement-submit" type="submit">确认补证</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'

import {
  dispatchReplay,
  precheckReplay,
  queryLineage,
  queryLineageGaps,
  revokeEvidence,
  supplementEvidence,
} from '@/api/lineage'
import type {
  DispatchResult,
  LineageCriteria,
  LineageQueryResult,
  LineageSegment,
  ReplayPrecheckResult,
} from '@/api/lineage'

const criteriaFields: (keyof LineageCriteria)[] = [
  '来源名称',
  '接入批次',
  '分区',
  '起始时间',
  '结束时间',
]

const criteria = reactive<LineageCriteria>({
  来源名称: 'orders-us',
  接入批次: 'B-20261120-01',
  分区: 'p1',
  起始时间: '2026-11-20T10:00:00+00:00',
  结束时间: '2026-11-20T11:00:00+00:00',
})

const queryResult = ref<LineageQueryResult | null>(null)
const precheckResult = ref<ReplayPrecheckResult | null>(null)
const dispatchResult = ref<DispatchResult | null>(null)
const feedback = ref('')
const feedbackKind = ref<'ok' | 'bad'>('ok')
const showSupplement = ref(false)
const supplementTarget = ref<LineageSegment | null>(null)
const supplementPartition = ref('')

function criteriaOf(segment: LineageSegment): LineageCriteria {
  return {
    来源名称: segment.来源名称,
    接入批次: segment.接入批次,
    分区: segment.分区,
    起始时间: criteria.起始时间,
    结束时间: criteria.结束时间,
  }
}

function formatCriteria(value: LineageCriteria): string {
  return `${value.来源名称} / ${value.接入批次} / ${value.分区} / ${value.起始时间} ~ ${value.结束时间}`
}

function setFeedback(message: string, ok = true) {
  feedback.value = message
  feedbackKind.value = ok ? 'ok' : 'bad'
}

function resetCriteria() {
  criteria.来源名称 = ''
  criteria.接入批次 = ''
  criteria.分区 = ''
  criteria.起始时间 = ''
  criteria.结束时间 = ''
  queryResult.value = null
  precheckResult.value = null
  dispatchResult.value = null
  feedback.value = ''
}

async function runQuery() {
  try {
    queryResult.value = await queryLineage({ ...criteria })
    setFeedback(queryResult.value.message, queryResult.value.ok)
  } catch (error) {
    setFeedback(error instanceof Error ? error.message : '血缘查询失败', false)
  }
}

async function runGapQuery() {
  try {
    queryResult.value = await queryLineageGaps({ ...criteria })
    setFeedback(queryResult.value.message, queryResult.value.ok)
  } catch (error) {
    setFeedback(error instanceof Error ? error.message : '缺口定位失败', false)
  }
}

async function runPrecheck(segment: LineageSegment) {
  try {
    precheckResult.value = await precheckReplay(criteriaOf(segment))
    setFeedback(precheckResult.value.message, precheckResult.value.ok)
  } catch (error) {
    setFeedback(error instanceof Error ? error.message : '回放预检失败', false)
  }
}

async function runDispatch(segment: LineageSegment) {
  try {
    dispatchResult.value = await dispatchReplay(criteriaOf(segment))
    setFeedback(dispatchResult.value.message, dispatchResult.value.ok)
    await runQuery()
  } catch (error) {
    setFeedback(error instanceof Error ? error.message : '回放派发失败', false)
  }
}

async function runRevoke(segment: LineageSegment) {
  try {
    const result = await revokeEvidence({
      来源名称: segment.来源名称,
      接入批次: segment.接入批次,
      分区: segment.分区,
    })
    setFeedback(result.message, result.ok)
    await runQuery()
  } catch (error) {
    setFeedback(error instanceof Error ? error.message : '撤销落地证明失败', false)
  }
}

function openSupplement(segment: LineageSegment) {
  supplementTarget.value = segment
  supplementPartition.value = `sink://dwd/orders/dt=2026-11-20/region=${segment.来源名称.split('-')[1]}/${segment.分区}`
  showSupplement.value = true
}

async function submitSupplement() {
  if (!supplementTarget.value) return
  try {
    const result = await supplementEvidence(
      {
        来源名称: supplementTarget.value.来源名称,
        接入批次: supplementTarget.value.接入批次,
        分区: supplementTarget.value.分区,
      },
      supplementPartition.value,
    )
    showSupplement.value = false
    setFeedback(result.message, result.ok)
    await runQuery()
  } catch (error) {
    setFeedback(error instanceof Error ? error.message : '补证失败', false)
  }
}
</script>

<style scoped>
.lineage-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
  margin-top: 18px;
}
.lineage-head h3 {
  margin: 0 0 4px;
}
.lineage-feedback {
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 13px;
}
.lineage-feedback.ok {
  background: #ecfdf3;
  color: #027a48;
}
.lineage-feedback.bad {
  background: #fef3f2;
  color: #b42318;
}
.lineage-table {
  margin-top: 12px;
}
.evidence-ok {
  color: #027a48;
  font-weight: 700;
}
.evidence-bad {
  color: #b42318;
  font-weight: 700;
}
.status-ok {
  color: #027a48;
}
.status-bad {
  color: #b42318;
}
.payload-line {
  white-space: nowrap;
}
.link.danger {
  color: #b42318;
}
.ambiguous-box,
.drill-box {
  margin-top: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  background: #fafbfc;
  font-size: 13px;
}
.ambiguous-box {
  border-color: #f5c2c0;
  background: #fff7f6;
}
</style>
