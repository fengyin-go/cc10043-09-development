"""血缘审计与回放演练的端到端场景测试（不依赖第三方库，直接驱动领域服务）。

演练脚本对应值班处置流程：
1. 值班员按四要素检索 A（orders-us/p1）和 B（orders-eu/p1）；
2. 预检勾选证据齐全的 A，尚未提交；
3. 上游撤销 A 的落地证明（撤销先于派发完成）；
4. 派发 A 必须被拦截，目的端收不到 A，A/B 的待核明细仍能定位；
5. 为 B 补齐落地分区关联后重新查询、预检、派发；
6. 全程核对候选消息明细与实际交付范围，并验证跨来源不能互相补全。
"""
from __future__ import annotations

import sys

from app.services.lineage import LINEAGE_QUERY_FIELDS, lineage_service
from app.store import store

BATCH = "B-20261120-01"
WINDOW = {"起始时间": "2026-11-20T10:00:00+00:00", "结束时间": "2026-11-20T11:00:00+00:00"}


def criteria(source: str, partition: str) -> dict[str, str]:
    return {"来源名称": source, "接入批次": BATCH, "分区": partition, **WINDOW}


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        raise SystemExit(1)


def main() -> None:
    # ---- 步骤 0：基线数据齐全，四个分段全部可按四要素定位 ----
    check("血缘证据表有 4 个分段", len(store.rows("lineage_evidence")) == 4)
    check("存在 1 条归属冲突载荷",
          sum(1 for row in store.rows("payload_store") if row.get("归属状态") == "冲突") == 1)

    # ---- 步骤 1：值班员检索 A 与 B 的血缘明细 ----
    query_a = lineage_service.query_lineage(criteria("orders-us", "p1"))
    query_b = lineage_service.query_lineage(criteria("orders-eu", "p1"))
    check("A 检索命中且仅 1 段", query_a["segment_total"] == 1)
    check("A 初始证据齐全", query_a["segments"][0]["证据齐全"] is True)
    check("B 检索命中且仅 1 段", query_b["segment_total"] == 1)
    check("B 初始缺少落地分区证据", query_b["segments"][0]["证据齐全"] is False
          and "落地分区" in query_b["segments"][0]["缺失证据"])
    check("缺证分段仍保留在血缘明细中", query_b["gap_total"] == 1)

    # 跨来源隔离：orders-eu 的查询里绝不出现 orders-us 的分段，反之亦然。
    for source, other in (("orders-us", "orders-eu"), ("orders-eu", "orders-us")):
        result = lineage_service.query_lineage(criteria(source, "p0"))
        sources = {segment["来源名称"] for segment in result["segments"]}
        check(f"{source} 查询不被 {other} 补全", other not in sources and sources <= {source},
              detail=str(sources))

    # 模糊来源名称不允许命中：四要素必须确切。
    fuzzy = lineage_service.query_lineage({**criteria("orders", "p1")})
    check("模糊来源名称 orders 不命中任何分段", fuzzy["ok"] and fuzzy["segment_total"] == 0)
    incomplete = lineage_service.query_lineage({"来源名称": "orders-us"})
    check("缺少批次/分区/时间的查询被拒绝", incomplete["ok"] is False
          and all(field in incomplete["message"] for field in
                  ("接入批次", "分区", "起始时间", "结束时间")))

    # ---- 步骤 2：对 A 做回放预检，证据齐全，候选消息确切 ----
    precheck_a = lineage_service.precheck_replay(criteria("orders-us", "p1"))
    check("A 预检通过", precheck_a["ok"] is True)
    check("A 候选消息仅 msg-us-p1-001",
          precheck_a["candidate_messages"] == ["msg-us-p1-001"],
          detail=str(precheck_a["candidate_messages"]))
    check("预检输出确切数据范围",
          precheck_a["exact_range"] == criteria("orders-us", "p1"))
    check("歧义载荷被预检排除",
          all("msg-ambiguous-001" not in precheck_a["candidate_messages"] for _ in (0,))
          and any(item["消息ID"] == "msg-ambiguous-001"
                  for item in precheck_a["ambiguous_payloads"]))

    # B 预检必须被拦截。
    precheck_b = lineage_service.precheck_replay(criteria("orders-eu", "p1"))
    check("B 预检因缺证被拦截", precheck_b["ok"] is False
          and len(precheck_b["blocked_segments"]) == 1
          and precheck_b["candidate_messages"] == [])

    # ---- 步骤 3：预检后、派发前，上游撤销 A 的落地证明 ----
    revoke = lineage_service.revoke_sink_evidence("orders-us", BATCH, "p1")
    check("A 落地证明撤销成功", revoke["ok"] is True)
    evidence_a = next(row for row in store.rows("lineage_evidence")
                      if row["分段标识"] == "orders-us/B-20261120-01/p1")
    check("撤销后证据行保留且状态为缺证",
          evidence_a["落地证明状态"] == "已撤销" and evidence_a["status"] == "缺证")
    check("撤销后证据版本递增", evidence_a["证据版本"] == 2)

    # ---- 步骤 4：派发 A —— 撤销先于派发完成，目的端不得收到 A ----
    dispatch_a = lineage_service.dispatch_replay(criteria("orders-us", "p1"))
    check("A 派发无消息交付", dispatch_a["delivered"] == [])
    check("A 分段被列入拦截明细",
          len(dispatch_a["refused"]) == 1
          and dispatch_a["refused"][0]["分段标识"] == "orders-us/B-20261120-01/p1"
          and "落地分区" in dispatch_a["refused"][0]["原因"])

    # A、B 的待核明细仍能通过缺口查询定位。
    gaps_a = lineage_service.list_gap_segments(criteria("orders-us", "p1"))
    gaps_b = lineage_service.list_gap_segments(criteria("orders-eu", "p1"))
    check("撤销后 A 仍可在缺口明细定位",
          [s["分段标识"] for s in gaps_a["segments"]] == ["orders-us/B-20261120-01/p1"])
    check("缺证 B 仍可在缺口明细定位",
          [s["分段标识"] for s in gaps_b["segments"]] == ["orders-eu/B-20261120-01/p1"])

    # ---- 步骤 5：为 B 补齐落地分区关联，再查询和派发 ----
    supplement = lineage_service.supplement_sink_evidence(
        "orders-eu", BATCH, "p1", "sink://dwd/orders/dt=2026-11-20/region=eu/p1")
    check("B 补证成功", supplement["ok"] is True)
    query_b_again = lineage_service.query_lineage(criteria("orders-eu", "p1"))
    check("补证后 B 证据齐全", query_b_again["segments"][0]["证据齐全"] is True)
    check("补证后 B 无缺口", query_b_again["gap_total"] == 0)

    precheck_b_again = lineage_service.precheck_replay(criteria("orders-eu", "p1"))
    check("B 补证后预检通过", precheck_b_again["ok"] is True
          and precheck_b_again["candidate_messages"] == ["msg-eu-p1-001"])

    dispatch_b = lineage_service.dispatch_replay(criteria("orders-eu", "p1"))
    check("B 实际交付 1 条消息", len(dispatch_b["delivered"]) == 1
          and dispatch_b["delivered"][0]["消息ID"] == "msg-eu-p1-001")
    check("B 交付范围与四要素完全一致",
          dispatch_b["delivery_range"] == criteria("orders-eu", "p1"))
    check("B 交付消息携带有效落地分区与证据版本",
          dispatch_b["delivered"][0]["落地分区"].endswith("region=eu/p1")
          and dispatch_b["delivered"][0]["证据版本"] == 2)
    check("B 派发无拦截", dispatch_b["refused"] == [])

    # ---- 步骤 6：全量核对——歧义载荷永不投递，跨来源数据不串段 ----
    dispatch_us_p0 = lineage_service.dispatch_replay(criteria("orders-us", "p0"))
    delivered_ids = {item["消息ID"] for item in dispatch_us_p0["delivered"]}
    check("orders-us/p0 交付两条归属唯一消息",
          delivered_ids == {"msg-us-p0-001", "msg-us-p0-002"})
    check("歧义载荷未进入任何交付",
          "msg-ambiguous-001" not in delivered_ids
          and all(item.get("消息ID") != "msg-ambiguous-001"
                  for item in dispatch_us_p0["delivered"]))
    check("orders-us 交付不含 eu 消息",
          all(item["来源名称"] == "orders-us" for item in dispatch_us_p0["delivered"]))

    # 全窗口逐段核对：四个分段状态与演练结论一致。
    expected = {
        "orders-us/B-20261120-01/p0": True,
        "orders-us/B-20261120-01/p1": False,   # 已撤销
        "orders-eu/B-20261120-01/p0": True,
        "orders-eu/B-20261120-01/p1": True,   # 已补证
    }
    for source in ("orders-us", "orders-eu"):
        for partition in ("p0", "p1"):
            result = lineage_service.query_lineage(criteria(source, partition))
            segment = result["segments"][0]
            check(f"分段 {segment['分段标识']} 齐全度={expected[segment['分段标识']]}",
                  segment["证据齐全"] is expected[segment["分段标识"]])

    print("\n全部演练断言通过。")


if __name__ == "__main__":
    if len(LINEAGE_QUERY_FIELDS) != 5:
        raise SystemExit("血缘四要素（含起止时间）定义异常")
    sys.exit(main())
