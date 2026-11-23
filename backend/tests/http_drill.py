"""HTTP 层端到端演练：通过真实 uvicorn 接口复现值班处置全流程。

运行前需在 8017 端口启动后端；只使用标准库，避免引入新依赖。
"""
from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8017/api"
BATCH = "B-20261120-01"
WINDOW = {"起始时间": "2026-11-20T10:00:00+00:00", "结束时间": "2026-11-20T11:00:00+00:00"}


def post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        raise SystemExit(1)


def crit(source: str, partition: str) -> dict:
    return {"values": {"来源名称": source, "接入批次": BATCH, "分区": partition, **WINDOW}}


def main() -> None:
    # 通用资源接口回归：原有审计列表与筛选不受影响。
    audit_page = get("/stream_audit?size=5")
    check("通用审计列表接口正常", audit_page["total"] >= 3 and len(audit_page["items"]) >= 3)

    # 1. 四要素不全的查询被接口拒绝。
    rejected = post("/lineage/query", {"values": {"来源名称": "orders-us"}})
    check("HTTP 缺条件查询被拒绝", rejected["ok"] is False and "缺少" in rejected["message"])

    # 2. 值班员检索 A 与 B。
    query_a = post("/lineage/query", crit("orders-us", "p1"))
    query_b = post("/lineage/query", crit("orders-eu", "p1"))
    check("A 初始证据齐全", query_a["segments"][0]["证据齐全"] is True)
    check("B 初始缺落地分区证据", "落地分区" in query_b["segments"][0]["缺失证据"])

    # 3. A 预检通过，B 预检拦截。
    precheck_a = post("/lineage/replay/precheck", crit("orders-us", "p1"))
    precheck_b = post("/lineage/replay/precheck", crit("orders-eu", "p1"))
    check("A 预检候选确切", precheck_a["candidate_messages"] == ["msg-us-p1-001"])
    check("B 预检被拦截", precheck_b["ok"] is False and precheck_b["candidate_messages"] == [])

    # 4. 撤销先于派发：撤销 A 的落地证明后再派发。
    revoked = post("/lineage/evidence/revoke",
                   {"values": {"来源名称": "orders-us", "接入批次": BATCH, "分区": "p1"}})
    check("A 落地证明撤销成功", revoked["ok"] is True)
    dispatch_a = post("/lineage/replay/dispatch", crit("orders-us", "p1"))
    check("目的端未收到 A", dispatch_a["delivered"] == [] and len(dispatch_a["refused"]) == 1)

    # 5. A/B 待核明细仍能通过缺口接口定位。
    gaps_a = post("/lineage/gaps", crit("orders-us", "p1"))
    gaps_b = post("/lineage/gaps", crit("orders-eu", "p1"))
    check("A 缺口可定位", [s["分段标识"] for s in gaps_a["segments"]] == ["orders-us/B-20261120-01/p1"])
    check("B 缺口可定位", [s["分段标识"] for s in gaps_b["segments"]] == ["orders-eu/B-20261120-01/p1"])

    # 6. 为 B 补证，再查询、预检、派发。
    supplemented = post("/lineage/evidence/supplement", {
        "values": {"来源名称": "orders-eu", "接入批次": BATCH, "分区": "p1"},
        "sink_partition": "sink://dwd/orders/dt=2026-11-20/region=eu/p1",
    })
    check("B 补证成功", supplemented["ok"] is True)
    precheck_b2 = post("/lineage/replay/precheck", crit("orders-eu", "p1"))
    check("B 补证后预检通过", precheck_b2["candidate_messages"] == ["msg-eu-p1-001"])
    dispatch_b = post("/lineage/replay/dispatch", crit("orders-eu", "p1"))
    check("B 实际交付 1 条且范围一致",
          [item["消息ID"] for item in dispatch_b["delivered"]] == ["msg-eu-p1-001"]
          and dispatch_b["delivery_range"] == crit("orders-eu", "p1")["values"])

    # 7. 跨来源隔离与歧义载荷拦截。
    query_eu = post("/lineage/query", crit("orders-eu", "p0"))
    check("orders-eu 查询不含 us 血缘",
          {s["来源名称"] for s in query_eu["segments"]} == {"orders-eu"})
    dispatch_eu = post("/lineage/replay/dispatch", crit("orders-eu", "p0"))
    check("歧义载荷未被投递",
          all(item["消息ID"] != "msg-ambiguous-001" for item in dispatch_eu["delivered"]))

    print("\nHTTP 演练全部通过。")


if __name__ == "__main__":
    main()
