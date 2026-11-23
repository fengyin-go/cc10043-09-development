"""血缘查询与回放演练接口。

所有接口都要求四要素定位（确切来源、接入批次、分区、时间窗口），
路由层只做参数收集，业务判断全部在 services/lineage.py 内完成。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.lineage import LINEAGE_QUERY_FIELDS, lineage_service

router = APIRouter(prefix="/api/lineage", tags=["血缘审计与回放演练"])


class LineageQueryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class EvidenceMutationPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    sink_partition: str | None = None


def _criteria(payload: LineageQueryPayload) -> dict[str, str]:
    return {field: str(payload.values.get(field, "")).strip() for field in LINEAGE_QUERY_FIELDS}


@router.post("/query")
def query_lineage(payload: LineageQueryPayload) -> dict[str, Any]:
    """按确切来源+批次+分区+时间返回逐段核对的血缘明细。"""
    return lineage_service.query_lineage(_criteria(payload))


@router.post("/gaps")
def query_gaps(payload: LineageQueryPayload) -> dict[str, Any]:
    """只定位处理缺口分段（缺证、撤销），保留已有证据不做跨来源补全。"""
    return lineage_service.list_gap_segments(_criteria(payload))


@router.post("/replay/precheck")
def replay_precheck(payload: LineageQueryPayload) -> dict[str, Any]:
    """回放预检：从血缘明细取得确切数据范围，输出候选/拦截分段与消息。"""
    return lineage_service.precheck_replay(_criteria(payload))


@router.post("/replay/dispatch")
def replay_dispatch(payload: LineageQueryPayload) -> dict[str, Any]:
    """派发实际消息：派发瞬间重新校验归属证据，失效即拦截。"""
    return lineage_service.dispatch_replay(_criteria(payload))


@router.post("/evidence/revoke")
def revoke_evidence(payload: EvidenceMutationPayload) -> dict[str, Any]:
    """模拟上游撤销落地证明（撤销先于派发完成的演练步骤）。"""
    values = payload.values
    return lineage_service.revoke_sink_evidence(
        str(values.get("来源名称", "")).strip(),
        str(values.get("接入批次", "")).strip(),
        str(values.get("分区", "")).strip(),
    )


@router.post("/evidence/supplement")
def supplement_evidence(payload: EvidenceMutationPayload) -> dict[str, Any]:
    """为缺证分段补齐落地分区关联（补证演练步骤）。"""
    values = payload.values
    return lineage_service.supplement_sink_evidence(
        str(values.get("来源名称", "")).strip(),
        str(values.get("接入批次", "")).strip(),
        str(values.get("分区", "")).strip(),
        payload.sink_partition or str(values.get("落地分区", "")).strip(),
    )
