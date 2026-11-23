"""血缘场景的结构化示例数据。

围绕两个确切来源 orders-us / orders-eu、接入批次 B-20261120-01 与分区
p0/p1 构造接入证据（检查点、落地分区、载荷归属），供流数据处理审计的
逐段血缘查询和批量消息回放的预检/派发使用。

与 seed.py 中占位示例分开维护：占位数据只保证通用列表页有内容，
这里的数据承载「确切来源+批次+分区+时间」的四要素定位规则。
"""
from __future__ import annotations

from typing import Any

# 统一的批次与时间窗口，所有证据和载荷都落在同一批次内，时间用于精确过滤。
BATCH = "B-20261120-01"
WINDOW_START = "2026-11-20T10:00:00+00:00"
WINDOW_END = "2026-11-20T11:00:00+00:00"

# 四个血缘分段：两个来源各自两个分区。证据状态覆盖齐全/缺证/冲突/撤销。
LINEAGE_SEGMENTS: list[dict[str, Any]] = [
    {
        "segment_id": "orders-us/B-20261120-01/p0",
        "来源名称": "orders-us",
        "接入批次": BATCH,
        "分区": "p0",
        "起始时间": "2026-11-20T10:00:00+00:00",
        "结束时间": "2026-11-20T10:30:00+00:00",
        "检查点值": "offset-5120",
        "落地分区": "sink://dwd/orders/dt=2026-11-20/region=us/p0",
        "消息ID": ["msg-us-p0-001", "msg-us-p0-002"],
        "初始落地状态": "有效",
        "说明": "证据齐全的基准分段",
    },
    {
        "segment_id": "orders-us/B-20261120-01/p1",
        "来源名称": "orders-us",
        "接入批次": BATCH,
        "分区": "p1",
        "起始时间": "2026-11-20T10:30:00+00:00",
        "结束时间": "2026-11-20T11:00:00+00:00",
        "检查点值": "offset-5121",
        "落地分区": "sink://dwd/orders/dt=2026-11-20/region=us/p1",
        "消息ID": ["msg-us-p1-001"],
        # 场景：值班员检索到的 A，预检通过后、派发前上游撤销其落地证明。
        "初始落地状态": "有效",
        "说明": "待撤销分段（演练中的 A）",
    },
    {
        "segment_id": "orders-eu/B-20261120-01/p0",
        "来源名称": "orders-eu",
        "接入批次": BATCH,
        "分区": "p0",
        "起始时间": "2026-11-20T10:00:00+00:00",
        "结束时间": "2026-11-20T10:30:00+00:00",
        "检查点值": "offset-8320",
        "落地分区": "sink://dwd/orders/dt=2026-11-20/region=eu/p0",
        "消息ID": ["msg-eu-p0-001", "msg-eu-p0-002"],
        "初始落地状态": "有效",
        "说明": "证据齐全的基准分段",
    },
    {
        "segment_id": "orders-eu/B-20261120-01/p1",
        "来源名称": "orders-eu",
        "接入批次": BATCH,
        "分区": "p1",
        "起始时间": "2026-11-20T10:30:00+00:00",
        "结束时间": "2026-11-20T11:00:00+00:00",
        "检查点值": "offset-8321",
        # 场景：演练中的 B，初始缺少落地分区证明，需要值班员补证。
        "落地分区": None,
        "消息ID": ["msg-eu-p1-001"],
        "初始落地状态": None,
        "说明": "缺证分段（演练中的 B），补证前不可回放",
    },
]

# 载荷与分段的归属关系。ambiguous 载荷同时挂两个来源，模拟
# 「历史载荷对应多个来源证明发生冲突」，任何回放都不得把它作为输入。
PAYLOAD_SPECS: list[dict[str, Any]] = [
    {"消息ID": "msg-us-p0-001", "来源名称": "orders-us", "归属状态": "唯一", "原始载荷": '{"order_id":"US-1001","region":"us","amount":120}'},
    {"消息ID": "msg-us-p0-002", "来源名称": "orders-us", "归属状态": "唯一", "原始载荷": '{"order_id":"US-1002","region":"us","amount":88}'},
    {"消息ID": "msg-us-p1-001", "来源名称": "orders-us", "归属状态": "唯一", "原始载荷": '{"order_id":"US-1003","region":"us","amount":240}'},
    {"消息ID": "msg-eu-p0-001", "来源名称": "orders-eu", "归属状态": "唯一", "原始载荷": '{"order_id":"EU-2001","region":"eu","amount":60}'},
    {"消息ID": "msg-eu-p0-002", "来源名称": "orders-eu", "归属状态": "唯一", "原始载荷": '{"order_id":"EU-2002","region":"eu","amount":75}'},
    {"消息ID": "msg-eu-p1-001", "来源名称": "orders-eu", "归属状态": "唯一", "原始载荷": '{"order_id":"EU-2003","region":"eu","amount":99}'},
    {
        "消息ID": "msg-ambiguous-001",
        "来源名称": "orders-us|orders-eu",
        "归属状态": "冲突",
        "原始载荷": '{"order_id":"X-9001","region":"unknown","amount":10}',
        "冲突来源": ["orders-us", "orders-eu"],
        "说明": "两个来源证明同时指向该载荷，归属有歧义，禁止作为回放输入",
    },
]


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def build_lineage_evidence() -> list[dict[str, Any]]:
    """生成血缘证据表：每个分段一行，汇聚检查点、落地分区、载荷三类证据。"""
    evidence_rows: list[dict[str, Any]] = []
    for index, segment in enumerate(LINEAGE_SEGMENTS, start=1):
        has_sink = segment["初始落地状态"] is not None
        evidence_rows.append({
            "id": index,
            "status": "有效" if has_sink else "缺证",
            "pending": not has_sink,
            "abnormal": not has_sink,
            "证据编号": f"EV-{segment['来源名称']}-{segment['分区']}",
            "来源名称": segment["来源名称"],
            "接入批次": segment["接入批次"],
            "分区": segment["分区"],
            "分段标识": segment["segment_id"],
            "检查点值": segment["检查点值"],
            "检查点状态": "已确认",
            "落地分区": segment["落地分区"] or "",
            "落地证明状态": segment["初始落地状态"] or "缺证",
            "起始时间": segment["起始时间"],
            "结束时间": segment["结束时间"],
            "关联消息ID": ",".join(segment["消息ID"]),
            "证据版本": 1,
            "撤销时间": "",
            "备注": segment["说明"],
        })
    return evidence_rows


def build_lineage_payloads(existing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在通用载荷示例之后追加带确切来源/批次/分区归属的历史载荷。"""
    next_id = _next_id(existing_rows)
    segment_index = {
        (segment["来源名称"], message_id): segment
        for segment in LINEAGE_SEGMENTS
        for message_id in segment["消息ID"]
    }
    payload_rows: list[dict[str, Any]] = []
    for spec in PAYLOAD_SPECS:
        message_id = spec["消息ID"]
        source = spec["来源名称"]
        segment = segment_index.get((source.split("|")[0], message_id))
        partition = segment["分区"] if segment else "pX"
        ambiguous = spec["归属状态"] == "冲突"
        payload_rows.append({
            "id": next_id,
            "status": "已存储",
            "pending": False,
            "abnormal": ambiguous,
            "采集批次": BATCH,
            "消息ID": message_id,
            "原始载荷": spec["原始载荷"],
            "存储时间": WINDOW_START,
            "压缩后大小": str(len(spec["原始载荷"])),
            # 血缘扩展字段：通用列表页会忽略，血缘/回放服务按它们精确关联。
            "来源名称": source,
            "分区": partition,
            "归属状态": spec["归属状态"],
            "冲突来源": ",".join(spec.get("冲突来源", [])),
            "起始时间": WINDOW_START,
            "结束时间": WINDOW_END,
        })
        next_id += 1
    return payload_rows


def install_lineage_seed(tables: dict[str, list[dict[str, Any]]]) -> None:
    """把血缘场景数据安装到内存仓库（幂等，重复调用不会叠加）。"""
    tables.setdefault("lineage_evidence", [])
    if not tables["lineage_evidence"]:
        tables["lineage_evidence"] = build_lineage_evidence()
    payload_table = tables.setdefault("payload_store", [])
    existing_ids = {row.get("消息ID") for row in payload_table}
    for row in build_lineage_payloads(payload_table):
        if row["消息ID"] not in existing_ids:
            payload_table.append(row)
            existing_ids.add(row["消息ID"])
