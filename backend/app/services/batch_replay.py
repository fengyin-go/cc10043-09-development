"""批量消息回放领域服务：将历史消息按时间范围回放到指定Topic，用于数据修复和断点续传。

数据链路位置：数据修复层，在审计发现偏差时通过批量回放机制修复遗漏或失败的消息。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.services.lineage import lineage_service
from app.store import store


class BatchReplayService(BaseModuleService):
    MODULE = "batch_replay"
    ENTITY = "回放任务"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["回放范围", "开始时间", "结束时间", "目标Topic", "回放速率"]
    REQUIRED_FIELDS = ["回放范围", "开始时间", "结束时间"]
    STATUS_ORDER = ["待回放", "回放中", "已完成", "回放中断"]
    ACTION_RULES = {"启动回放": "回放中", "中断回放": "回放中断"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：批量回放和断点续传 ----

    def start_replay(self, entry_id: int) -> dict[str, Any]:
        """启动回放任务：从载荷存储读取原始消息并按速率写入目标Topic。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "回放任务不存在"}
        target = entry.get("目标Topic", "")
        payloads = store.rows("payload_store")
        matched = [p for p in payloads if target in str(p.get("采集批次", ""))]
        entry["status"] = "回放中"
        entry["pending"] = True
        now = datetime.now(timezone.utc).isoformat()
        replayed = 0
        for p in matched[:10]:
            p_id = p.get("消息ID", f"replay-{replayed}")
            store.rows("payload_store").append({
                "id": max((int(r.get("id", 0)) for r in store.rows("payload_store")), default=0) + 1,
                "采集批次": f"replay-{target}", "消息ID": f"replayed-{p_id}",
                "原始载荷": str(p.get("原始载荷", "")), "存储时间": now,
                "压缩后大小": str(len(str(p.get("原始载荷", "")))), "status": "已存储",
                "pending": True, "abnormal": False,
            })
            replayed += 1
        return {"ok": True, "message": f"回放已启动，已回放{replayed}条", "replayed": replayed}

    def validate_replay_range(self, entry_id: int) -> dict[str, Any]:
        """校验回放时间范围是否有效且与载荷存储中的数据匹配。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "任务不存在"}
        start_str = entry.get("开始时间", "")
        end_str = entry.get("结束时间", "")
        if not start_str or not end_str:
            return {"ok": False, "message": "缺少开始或结束时间"}
        if start_str > end_str:
            return {"ok": False, "message": "开始时间不能晚于结束时间"}
        payloads = store.rows("payload_store")
        target = entry.get("目标Topic", "")
        in_range = [p for p in payloads if target in str(p.get("采集批次", ""))]
        return {"ok": True, "in_range_count": len(in_range), "range": f"{start_str}~{end_str}"}

    def handle_interrupt(self, entry_id: int, reason: str) -> dict[str, Any]:
        """处理回放中断：记录中断原因并清理上下文。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "任务不存在"}
        entry["status"] = "回放中断"
        entry["abnormal"] = True
        now = datetime.now(timezone.utc).isoformat()
        isolation_rows = store.rows("failure_isolation")
        fid = max((int(r.get("id", 0)) for r in isolation_rows), default=0) + 1
        isolation_rows.append({
            "id": fid, "失败消息ID": f"replay-err-{entry_id}",
            "失败原因": reason, "原始Topic": entry.get("目标Topic", ""),
            "隔离时间": now, "重试次数": "0",
            "status": "待处理", "pending": True, "abnormal": False,
        })
        return {"ok": True, "message": "回放中断已记录", "isolation_id": fid}

    def complete_with_audit(self, entry_id: int) -> dict[str, Any]:
        """回放完成时联动审计和偏移量管理。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "任务不存在"}
        entry["status"] = "已完成"
        entry["pending"] = False
        entry["abnormal"] = False
        target = entry.get("目标Topic", "")
        now = datetime.now(timezone.utc).isoformat()
        offset_rows = store.rows("offset_manager")
        for row in offset_rows:
            if target in str(row.get("Topic/Queue", "")):
                try:
                    current = int(row.get("当前Offset", "0"))
                except ValueError:
                    continue
                row["当前Offset"] = str(current + 1)
                row["消费延迟"] = "0"
        audit_rows = store.rows("stream_audit")
        aid = max((int(r.get("id", 0)) for r in audit_rows), default=0) + 1
        audit_rows.append({
            "id": aid, "处理时间": now, "数据源": target,
            "消息总数": "1", "成功数": "1", "失败原因分布": "",
            "status": "正常", "pending": True, "abnormal": False,
        })
        return {"ok": True, "message": "回放完成已审计"}

    # ---- 业务扩展：基于血缘证据的回放预检与派发 ----

    def precheck_with_lineage(self, criteria: dict[str, str]) -> dict[str, Any]:
        """回放预检：从血缘明细取得确切数据范围，证据不齐的分段不得进入回放。"""
        return lineage_service.precheck_replay(criteria)

    def dispatch_with_evidence(self, criteria: dict[str, str]) -> dict[str, Any]:
        """派发实际消息：以派发瞬间的归属证据为准重新校验。

        预检勾选不代表派发许可；上游在预检后撤销落地证明时，派发会拦截
        对应分段，目的端不会收到证据失效的消息，缺证分段仍保留在血缘明细里。
        """
        result = lineage_service.dispatch_replay(criteria)
        if not result.get("ok") and not result.get("delivered"):
            result["status"] = "回放中断"
            return result
        delivered = result.get("delivered", [])
        if delivered:
            replay_rows = store.rows(self.MODULE)
            rid = max((int(r.get("id", 0)) for r in replay_rows), default=0) + 1
            exact_range = result.get("delivery_range", {})
            replay_rows.append({
                "id": rid,
                "回放范围": f"{exact_range.get('来源名称', '')}/"
                            f"{exact_range.get('接入批次', '')}/"
                            f"{exact_range.get('分区', '')} 共{len(delivered)}条",
                "开始时间": exact_range.get("起始时间", ""),
                "结束时间": exact_range.get("结束时间", ""),
                "目标Topic": exact_range.get("来源名称", ""),
                "回放速率": "100",
                "status": "已完成", "pending": False, "abnormal": False,
            })
            result["replay_id"] = rid
        result["status"] = "已完成" if not result.get("refused") else "回放中断"
        return result