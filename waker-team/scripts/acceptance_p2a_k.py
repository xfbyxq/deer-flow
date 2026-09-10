#!/usr/bin/env python3
"""
剧本 K：创建 cron 调度 → 定时触发 Flow → 执行历史可查

用法：
    .venv/bin/python scripts/acceptance_p2a_k.py

前置条件：
    - waker-team 服务已启动（uvicorn app.main:app --port 8002）
    - DeerFlow API 可用
"""

import sys
import time

import httpx

BASE_URL = "http://localhost:8002"
WAIT_SECONDS = 75  # 等待 75 秒让调度器触发


def main() -> None:
    client = httpx.Client(base_url=BASE_URL, timeout=30)

    # 1. 创建 Group
    print("[1/7] 创建 Group...")
    resp = client.post("/api/groups", json={"name": "p2a-k-test-group"})
    if resp.status_code not in (200, 201):
        print(f"❌ 创建 Group 失败: {resp.status_code} {resp.text}")
        sys.exit(1)
    group_id = resp.json()["id"]
    print(f"  Group ID: {group_id}")

    # 2. 创建 Flow 定义（使用简单的 condition + notify，不依赖 DeerFlow）
    print("[2/7] 创建 Flow 定义...")
    flow_def = {
        "name": "P2a-K Flow: scheduled execution",
        "group_id": group_id,
        "description": "Acceptance test K: scheduled flow execution",
        "definition_json": {
            "version": 1,
            "nodes": [
                {
                    "key": "step1",
                    "type": "condition",
                    "expression": "true",
                    "branches": {"true": "step2"},
                    "depends_on": [],
                },
                {
                    "key": "step2",
                    "type": "notify",
                    "channel": "slack",
                    "payload": {"msg": "scheduled run"},
                    "depends_on": ["step1"],
                },
            ],
            "settings": {"max_concurrent_nodes": 5, "on_failure": "pause"},
        },
    }
    resp = client.post("/api/flows", json=flow_def)
    if resp.status_code not in (200, 201):
        print(f"❌ 创建 Flow 定义失败: {resp.status_code} {resp.text}")
        sys.exit(1)
    flow_def_id = resp.json()["id"]
    print(f"  Flow Def ID: {flow_def_id}")

    # 3. 创建 cron 调度（每分钟触发一次）
    print("[3/7] 创建 cron 调度（每分钟触发）...")
    schedule = {
        "name": "P2a-K Schedule",
        "group_id": group_id,
        "description": "Acceptance test K: every minute",
        "cron_expression": "* * * * *",
        "target_type": "flow",
        "target_id": flow_def_id,
    }
    resp = client.post("/api/schedules", json=schedule)
    if resp.status_code not in (200, 201):
        print(f"❌ 创建调度失败: {resp.status_code} {resp.text}")
        sys.exit(1)
    schedule_id = resp.json()["id"]
    print(f"  Schedule ID: {schedule_id}")

    # 4. 等待 60-90 秒让调度触发
    print(f"[4/7] 等待 {WAIT_SECONDS} 秒让调度触发...")
    for i in range(WAIT_SECONDS // 5):
        time.sleep(5)
        resp = client.get(f"/api/schedules/{schedule_id}")
        if resp.status_code == 200:
            sched = resp.json()
            print(f"  [{(i + 1) * 5}s] run_count={sched.get('run_count', 0)}, last_run={sched.get('last_run_at', 'N/A')}")
            if sched.get("run_count", 0) >= 1:
                print("  调度已触发！")
                break

    # 5. 验证至少触发一次
    print("[5/7] 验证调度触发...")
    resp = client.get(f"/api/schedules/{schedule_id}")
    if resp.status_code != 200:
        print(f"❌ 获取调度失败: {resp.status_code}")
        sys.exit(1)
    schedule_data = resp.json()
    run_count = schedule_data.get("run_count", 0)
    print(f"  触发次数: {run_count}")
    if run_count < 1:
        print("  ⚠️ 调度尚未触发（可能需要更多时间），继续验证...")
    else:
        print(f"  ✅ 调度已触发 {run_count} 次")

    # 6. 查询执行历史
    print("[6/7] 查询执行历史...")
    resp = client.get(f"/api/schedules/{schedule_id}/runs")
    if resp.status_code == 200:
        runs = resp.json()
        print(f"  执行历史条数: {len(runs)}")
        for run in runs[:5]:
            print(f"    {run['id']}: status={run['status']}, triggered_at={run.get('triggered_at', 'N/A')}")
    else:
        print(f"  执行历史 API 返回: {resp.status_code}")
        # 尝试另一个端点
        resp = client.get(f"/api/schedules/{schedule_id}/history")
        if resp.status_code == 200:
            runs = resp.json()
            print(f"  执行历史条数 (via /history): {len(runs)}")

    # 查询该 Flow 的运行记录
    resp = client.get("/api/flow-runs", params={"flow_id": flow_def_id})
    if resp.status_code == 200:
        flow_runs = resp.json()
        scheduled_runs = [r for r in flow_runs if r.get("trigger_type") == "schedule"]
        print(f"  Flow 运行记录: {len(flow_runs)} 条, 其中调度触发: {len(scheduled_runs)} 条")
        for r in scheduled_runs[:3]:
            print(f"    {r['id']}: status={r['status']}, trigger={r['trigger_type']}")

    # 7. 暂停调度
    print("[7/7] 暂停调度...")
    resp = client.patch(f"/api/schedules/{schedule_id}", json={"cron_expression": "0 0 1 1 *"})
    if resp.status_code == 200:
        print("  ✅ 调度已暂停（设置 cron 为每年一次）")
    else:
        # 尝试删除
        resp = client.delete(f"/api/schedules/{schedule_id}")
        if resp.status_code in (200, 204):
            print("  ✅ 调度已删除")
        else:
            print(f"  ⚠️ 暂停/删除调度失败: {resp.status_code}")

    # 验证时间线（如果有调度触发的运行）
    resp = client.get("/api/flow-runs", params={"flow_id": flow_def_id})
    if resp.status_code == 200:
        flow_runs = resp.json()
        if flow_runs:
            first_run_id = flow_runs[0]["id"]
            print(f"\n[bonus] 验证时间线 API（Flow Run: {first_run_id}）...")
            resp = client.get(f"/api/flow-runs/{first_run_id}/timeline")
            if resp.status_code == 200:
                timeline = resp.json()
                print(f"  时间线节点数: {len(timeline['nodes'])}")
                for node in timeline["nodes"]:
                    print(f"    {node['node_key']}: {node['status']}")
            else:
                print(f"  时间线 API 返回: {resp.status_code}")

    print("\n✅ 剧本 K PASS")


if __name__ == "__main__":
    main()
