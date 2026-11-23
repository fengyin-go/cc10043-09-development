"""血缘查询与回放预检的共享领域逻辑。

设计约束（对应审计演练规则）：

1. 一次查询必须同时给出确切来源、接入批次、分区和时间范围，四个条件全部
   精确匹配；orders-us 与 orders-eu 互相不能补全对方的血缘。
2. 血缘明细按「分段」返回，逐段给出检查点证据、落地分区证据和归属载荷，
   缺证分段保留在结果里，不允许用其他来源/分区的数据补齐。
3. 历史载荷对应多个来源证明（归属冲突）时，保留缺证分段和已有证据，
   歧义载荷只做标注，不能作为回放输入。
4. 回放预检从血缘明细取得确切数据范围；真正派发消息时要重新校验归属
   证据仍然有效（可能在预检后、派发前被上游撤销）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

# 血缘四要素：查询时缺一不可，且全部精确等值/区间匹配。
LINEAGE_QUERY_FIELDS = ("来源名称", "接入批次", "分区", "起始时间", "结束时间")
EVIDENCE_TYPES = ("检查点", "落地分区", "载荷归属")


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _in_window(segment_start: str, segment_end: str, window_start: str, window_end: str) -> bool:
    """分段时间与查询窗口有交集即命中；解析失败时退化为字符串精确比较。"""
    s_start, s_end = _parse_time(segment_start), _parse_time(segment_end)
    w_start, w_end = _parse_time(window_start), _parse_time(window_end)
    if None in (s_start, s_end, w_start, w_end):
        return segment_start == window_start and segment_end == window_end
    return s_start <= w_end and s_end >= w_start


class LineageService:
    """血缘证据查询、证据撤销/补证、回放预检与派发的纯领域逻辑。"""

    # ---- 证据写入侧：撤销与补证（模拟上游证明变化） ----

    def revoke_sink_evidence(self, source: str, batch: str, partition: str) -> dict[str, Any]:
        """上游撤销某分段的落地证明：证据行保留，状态置为「已撤销」。"""
        evidence = self._find_evidence(source, batch, partition)
        if evidence is None:
            return {"ok": False, "message": "未找到对应血缘证据，无法撤销"}
        evidence["落地证明状态"] = "已撤销"
        evidence["落地分区"] = ""
        evidence["撤销时间"] = datetime.now().astimezone().isoformat()
        evidence["证据版本"] = int(evidence.get("证据版本", 1)) + 1
        evidence["status"] = "缺证"
        evidence["pending"] = True
        evidence["abnormal"] = True
        return {"ok": True, "message": "落地证明已撤销，分段保留可继续定位", "segment": evidence["分段标识"]}

    def supplement_sink_evidence(
        self, source: str, batch: str, partition: str, sink_partition: str
    ) -> dict[str, Any]:
        """为缺证分段补齐落地分区证明，补证只作用于确切的四要素分段。"""
        evidence = self._find_evidence(source, batch, partition)
        if evidence is None:
            return {"ok": False, "message": "未找到对应血缘证据，无法补证"}
        if not sink_partition.strip():
            return {"ok": False, "message": "补证必须给出确切落地分区"}
        evidence["落地分区"] = sink_partition.strip()
        evidence["落地证明状态"] = "有效"
        evidence["撤销时间"] = ""
        evidence["证据版本"] = int(evidence.get("证据版本", 1)) + 1
        evidence["status"] = "有效"
        evidence["pending"] = False
        evidence["abnormal"] = False
        return {"ok": True, "message": "落地证明已补齐", "segment": evidence["分段标识"]}

    # ---- 查询侧：四要素精确定位的逐段血缘明细 ----

    def query_lineage(self, criteria: dict[str, str]) -> dict[str, Any]:
        missing = [field for field in LINEAGE_QUERY_FIELDS if not str(criteria.get(field) or "").strip()]
        if missing:
            return {
                "ok": False,
                "message": f"血缘查询必须提供确切筛选条件，缺少：{'、'.join(missing)}",
                "segments": [],
            }
        source = criteria["来源名称"].strip()
        batch = criteria["接入批次"].strip()
        partition = criteria["分区"].strip()
        window_start = criteria["起始时间"].strip()
        window_end = criteria["结束时间"].strip()
        if _parse_time(window_start) and _parse_time(window_end) and window_start > window_end:
            return {"ok": False, "message": "起始时间不能晚于结束时间", "segments": []}

        evidence_rows = store.rows("lineage_evidence")
        # 全部精确等值匹配：来源名称不做模糊包含，orders-us 查不到 orders-eu 的证据。
        exact = [
            row for row in evidence_rows
            if row.get("来源名称") == source
            and row.get("接入批次") == batch
            and row.get("分区") == partition
        ]
        segments = [
            self._build_segment(row, window_start, window_end)
            for row in exact
            if _in_window(str(row.get("起始时间", "")), str(row.get("结束时间", "")),
                          window_start, window_end)
        ]
        # 歧义载荷只标注、不剔除：保留缺证分段和已有证据，回放时再拦截。
        ambiguous_payloads = [
            {
                "消息ID": row.get("消息ID"),
                "来源名称": row.get("来源名称"),
                "冲突来源": str(row.get("冲突来源", "")).split(","),
                "说明": "同一载荷存在多个来源证明，归属有歧义，禁止作为回放输入",
            }
            for row in store.rows("payload_store")
            if row.get("归属状态") == "冲突"
        ]
        complete_segments = [segment for segment in segments if segment["证据齐全"]]
        return {
            "ok": True,
            "criteria": {
                "来源名称": source, "接入批次": batch, "分区": partition,
                "起始时间": window_start, "结束时间": window_end,
            },
            "segments": segments,
            "segment_total": len(segments),
            "complete_total": len(complete_segments),
            "gap_total": len(segments) - len(complete_segments),
            "ambiguous_payloads": ambiguous_payloads,
            "message": self._summarize(source, partition, segments),
        }

    def list_gap_segments(self, criteria: dict[str, str]) -> dict[str, Any]:
        """只返回处理缺口分段：证据被撤销/缺证的分段仍然可定位。"""
        result = self.query_lineage(criteria)
        if not result.get("ok"):
            return result
        gaps = [segment for segment in result["segments"] if not segment["证据齐全"]]
        result["segments"] = gaps
        result["segment_total"] = len(gaps)
        result["message"] = f"定位到 {len(gaps)} 个缺证/撤销分段"
        return result

    # ---- 回放侧：预检取确切范围，派发时二次校验证据有效性 ----

    def precheck_replay(self, criteria: dict[str, str]) -> dict[str, Any]:
        """回放预检：从血缘明细取得确切数据范围并校验证据齐全度。"""
        result = self.query_lineage(criteria)
        if not result.get("ok"):
            return result
        ready: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for segment in result["segments"]:
            target = ready if segment["证据齐全"] else blocked
            target.append({
                "分段标识": segment["分段标识"],
                "缺失证据": segment["缺失证据"],
                "候选消息": [p["消息ID"] for p in segment["归属载荷"] if p["归属状态"] == "唯一"],
            })
        candidate_messages = [
            payload["消息ID"]
            for segment in result["segments"]
            for payload in segment["归属载荷"]
            if segment["证据齐全"] and payload["归属状态"] == "唯一"
        ]
        return {
            "ok": len(ready) > 0,
            "message": (
                f"预检通过 {len(ready)} 段，拦截 {len(blocked)} 段；"
                f"候选消息 {len(candidate_messages)} 条，歧义载荷已排除"
            ),
            "ready_segments": ready,
            "blocked_segments": blocked,
            "candidate_messages": candidate_messages,
            "exact_range": result["criteria"],
            "ambiguous_payloads": result["ambiguous_payloads"],
        }

    def dispatch_replay(self, criteria: dict[str, str]) -> dict[str, Any]:
        """派发实际消息：以血缘证据的当前状态为准，重新校验后再投递。

        预检勾选不构成派发许可——若证据在预检后被撤销，这里会重新拦截，
        保证目的端不会收到证据已失效分段的消息。
        """
        result = self.query_lineage(criteria)
        if not result.get("ok"):
            return result
        delivered: list[dict[str, Any]] = []
        refused: list[dict[str, Any]] = []
        for segment in result["segments"]:
            if not segment["证据齐全"]:
                refused.append({
                    "分段标识": segment["分段标识"],
                    "原因": f"证据缺失：{'、'.join(segment['缺失证据']) or '归属证据已失效'}",
                })
                continue
            for payload in segment["归属载荷"]:
                if payload["归属状态"] != "唯一":
                    refused.append({
                        "分段标识": segment["分段标识"],
                        "消息ID": payload["消息ID"],
                        "原因": "载荷归属存在多来源冲突，禁止回放",
                    })
                    continue
                delivered.append({
                    "消息ID": payload["消息ID"],
                    "来源名称": segment["来源名称"],
                    "接入批次": segment["接入批次"],
                    "分区": segment["分区"],
                    "落地分区": segment["落地分区证据"]["落地分区"],
                    "证据版本": segment["证据版本"],
                })
        return {
            "ok": len(delivered) > 0,
            "message": f"实际投递 {len(delivered)} 条，拦截 {len(refused)} 条",
            "delivered": delivered,
            "refused": refused,
            "delivery_range": result["criteria"],
        }

    # ---- 内部工具 ----

    def _find_evidence(self, source: str, batch: str, partition: str) -> dict[str, Any] | None:
        for row in store.rows("lineage_evidence"):
            if (row.get("来源名称") == source and row.get("接入批次") == batch
                    and row.get("分区") == partition):
                return row
        return None

    def _build_segment(self, evidence: dict[str, Any], window_start: str, window_end: str) -> dict[str, Any]:
        """把证据行与载荷存储按四要素关联，组装成可逐段核对的血缘明细。"""
        source = str(evidence.get("来源名称", ""))
        batch = str(evidence.get("接入批次", ""))
        partition = str(evidence.get("分区", ""))
        related_ids = [value for value in str(evidence.get("关联消息ID", "")).split(",") if value]

        checkpoint_ok = evidence.get("检查点状态") == "已确认" and bool(evidence.get("检查点值"))
        sink_ok = evidence.get("落地证明状态") == "有效" and bool(evidence.get("落地分区"))

        payloads: list[dict[str, Any]] = []
        for row in store.rows("payload_store"):
            if row.get("消息ID") not in related_ids:
                continue
            # 归属证据必须与查询来源精确一致；冲突载荷保留并标注，不做补全。
            ownership_ok = row.get("来源名称") == source and row.get("归属状态") == "唯一"
            payloads.append({
                "消息ID": row.get("消息ID"),
                "来源名称": row.get("来源名称"),
                "归属状态": row.get("归属状态"),
                "归属有效": ownership_ok,
                "冲突来源": str(row.get("冲突来源", "")).split(",") if row.get("冲突来源") else [],
                "存储时间": row.get("存储时间"),
            })
        payload_ok = bool(payloads) and all(item["归属有效"] for item in payloads)

        missing: list[str] = []
        if not checkpoint_ok:
            missing.append("检查点")
        if not sink_ok:
            missing.append("落地分区")
        if not payload_ok:
            missing.append("载荷归属")

        return {
            "分段标识": evidence.get("分段标识"),
            "来源名称": source,
            "接入批次": batch,
            "分区": partition,
            "起始时间": evidence.get("起始时间"),
            "结束时间": evidence.get("结束时间"),
            "查询窗口": {"起始时间": window_start, "结束时间": window_end},
            "检查点证据": {
                "检查点值": evidence.get("检查点值"),
                "检查点状态": evidence.get("检查点状态"),
                "有效": checkpoint_ok,
            },
            "落地分区证据": {
                "落地分区": evidence.get("落地分区"),
                "落地证明状态": evidence.get("落地证明状态"),
                "撤销时间": evidence.get("撤销时间", ""),
                "有效": sink_ok,
            },
            "归属载荷": payloads,
            "证据版本": evidence.get("证据版本"),
            "缺失证据": missing,
            "证据齐全": not missing,
        }

    @staticmethod
    def _summarize(source: str, partition: str, segments: list[dict[str, Any]]) -> str:
        if not segments:
            return f"来源 {source} 分区 {partition} 在给定批次与时间窗口内无血缘分段（不会用其他来源补全）"
        gaps = sum(1 for segment in segments if not segment["证据齐全"])
        if gaps == 0:
            return f"来源 {source} 分区 {partition} 共 {len(segments)} 段，证据齐全"
        return f"来源 {source} 分区 {partition} 共 {len(segments)} 段，其中 {gaps} 段存在证据缺口"


lineage_service = LineageService()
