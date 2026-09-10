#!/usr/bin/env python3
"""M0 冒烟 S2/S3/S5/S6：通过真实 run 验证 MCP stub（m0-stub）行为。

用法（凭据环境变量注入）:
    DEERFLOW_SMOKE_EMAIL=... DEERFLOW_SMOKE_PASSWORD=... backend/.venv/bin/python \
        docs/companion/m0-smoke/run_mcp_smoke.py --stage forward|deny|timeout|idem|cleanup

阶段:
    forward : 建 runner agent+thread，run 带 context.secrets 调 m0-stub_m0_echo —— S3 可见性 + S6 正向 + S4 终态字段观察
    deny    : 同一 thread 再 run（不带 secrets）调工具 —— S6 fail-closed（on_missing: deny）
    timeout : run 要求 m0_sleep(10) —— S2 HTTP transport 实际等待边界（成功=等待>10s；失败=断点≤10s）
    idem    : 同 Idempotency-Key 重发 —— S5 幂等复用
    cleanup : 删除 runner agent / 恢复 extensions_config 由外部脚本做，此处只删 agent+thread
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import httpx

BASE_URL = os.environ.get("DEERFLOW_SMOKE_BASE_URL", "http://127.0.0.1:2026")
AGENT_NAME = "m0-smoke-runner"
THREAD_ID = "m0-smoke-thread"
IDEM_KEY = "m0-idem-key-1"

results: list[str] = []


def _login(client: httpx.Client) -> None:
    email = os.environ["DEERFLOW_SMOKE_EMAIL"]
    password = os.environ["DEERFLOW_SMOKE_PASSWORD"]
    r = client.post("/api/v1/auth/login/local", data={"username": email, "password": password, "remember_me": "true"})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"


def _csrf(client: httpx.Client) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("csrf_token")}


def _run_body(message: str, *, secrets: bool, agent: str = AGENT_NAME) -> dict:
    body: dict = {
        "input": {"messages": [{"role": "user", "content": message}]},
        "config": {"configurable": {"agent_name": agent}},
    }
    if secrets:
        body["config"]["context"] = {"secrets": {"waker_identity": "waker-a@m0-smoke"}}
    return body


def _wait_run(client: httpx.Client, thread_id: str, run_id: str, timeout_s: float = 240.0) -> dict:
    """轮询 GET /runs/{id} 直至终态，返回最终记录。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = client.get(f"/api/threads/{thread_id}/runs/{run_id}")
        if r.status_code != 200:
            return {"poll_error": f"{r.status_code} {r.text[:200]}"}
        rec = r.json()
        st = rec.get("status")
        if st in ("success", "error", "timeout", "interrupted", "cancelled"):
            return rec
        time.sleep(2)
    return {"poll_timeout": True}


def _stage_forward(client: httpx.Client, fresh: bool) -> None:
    # 建 runner agent + thread（幂等：存在即跳过；fresh=删旧 thread 重建）
    h = _csrf(client)
    r = client.post("/api/agents", json={"name": AGENT_NAME, "description": "m0 smoke runner", "soul": "你是冒烟测试执行助手，严格按指令调用工具并简短汇报。"}, headers=h)
    print(f"[INFO] create agent: {r.status_code}（409=已存在，忽略）")
    if r.status_code not in (200, 201, 409):
        print(r.text[:300]); return
    if fresh:
        client.delete(f"/api/threads/{THREAD_ID}", headers=h)
        print("[INFO] fresh: 旧 thread 已删除")
    r = client.post("/api/threads", json={"thread_id": THREAD_ID}, headers=h)
    print(f"[INFO] create thread: {r.status_code}")
    # run1: secrets 注入 + m0_echo（S3 可见性 + S6 正向 + S4 终态）
    body = _run_body(
        "调用工具 m0-stub_m0_echo，text 参数传 'hello-m0'。若该工具不可用，请直接说明你可见的工具名。"
        "不要调用其他工具，结果简短即可。",
        secrets=True,
    )
    r = client.post(f"/api/threads/{THREAD_ID}/runs", json=body, headers=h)
    if r.status_code not in (200, 201):
        print(f"[FAIL] run1 start: {r.status_code} {r.text[:300]}"); return
    run_id = r.json().get("run_id")
    print(f"[INFO] run1 started: {run_id}")
    rec = _wait_run(client, THREAD_ID, run_id)
    st = rec.get("status")
    print(f"[{'PASS' if st == 'success' else 'FAIL'}] S4 终态字段: status={st} keys={sorted(rec.keys())}")
    if rec.get("error"):
        print(f"  error: {str(rec['error'])[:300]}")
    # 从 thread state 取最后 assistant 消息看是否提到工具结果
    s = client.get(f"/api/threads/{THREAD_ID}/state")
    if s.status_code == 200:
        text = str(s.json())[:2000]
        print(f"[INFO] S6 证据(含 echo/headers 片段): {'echo' in text and 'headers' in text}")
        import re
        m = re.search(r'"echo":\s*"[^"]*"', text)
        print(f"  echo 结果: {m.group(0) if m else '未找到（可能被截断或未调用成功）'}")
    else:
        print(f"[INFO] state 端点: {s.status_code}（S8 时再探测）")


def _stage_deny(client: httpx.Client) -> None:
    body = _run_body("调用工具 m0-stub_m0_echo，text='deny-test'，然后汇报工具是否可用。", secrets=False)
    r = client.post(f"/api/threads/{THREAD_ID}/runs", json=body, headers=_csrf(client))
    run_id = r.json().get("run_id") if r.status_code in (200, 201) else None
    if not run_id:
        print(f"[FAIL] deny run start: {r.status_code} {r.text[:300]}"); return
    rec = _wait_run(client, THREAD_ID, run_id)
    st = rec.get("status")
    err = str(rec.get("error") or "")
    denied = "deny" in err.lower() or "missing" in err.lower() or "waker_identity" in err
    print(f"[{'PASS' if denied else 'FAIL'}] S6 fail-closed(deny): run status={st} denied_hint={denied}")
    print(f"  error: {err[:300]}")


def _stage_timeout(client: httpx.Client) -> None:
    body = _run_body("调用工具 m0-stub_m0_sleep，seconds=10，然后汇报实际等待感知。", secrets=True)
    t0 = time.monotonic()
    r = client.post(f"/api/threads/{THREAD_ID}/runs", json=body, headers=_csrf(client))
    run_id = r.json().get("run_id") if r.status_code in (200, 201) else None
    if not run_id:
        print(f"[FAIL] timeout run start: {r.status_code} {r.text[:300]}"); return
    rec = _wait_run(client, THREAD_ID, run_id, timeout_s=300)
    st = rec.get("status")
    err = str(rec.get("error") or "")
    print(f"[INFO] S2 边界: status={st} 耗时={time.monotonic()-t0:.0f}s error={err[:200]}")
    ok = st == "success"
    print(f"[{'PASS' if ok else 'WARN'}] S2 10s 挂起{'可等待(上限>10s)' if ok else '被中断(上限<=10s，需二分定位)'} → 结论见 m0-decisions")


def _stage_idem(client: httpx.Client) -> None:
    body = _run_body("只回复 OK 两个字母，不要调用任何工具。", secrets=True)
    h = {**_csrf(client), "Idempotency-Key": IDEM_KEY}
    r1 = client.post(f"/api/threads/{THREAD_ID}/runs", json=body, headers=h)
    id1 = r1.json().get("run_id") if r1.status_code in (200, 201) else None
    r2 = client.post(f"/api/threads/{THREAD_ID}/runs", json=body, headers=h)
    id2 = r2.json().get("run_id") if r2.status_code in (200, 201) else None
    reused = id1 and id1 == id2
    print(f"[{'PASS' if reused else 'FAIL'}] S5 Idempotency-Key 复用: first={id1} second={id2} reused={reused}")
    if id1 and not reused:
        _wait_run(client, THREAD_ID, id1)


def _stage_cleanup(client: httpx.Client) -> None:
    h = _csrf(client)
    r = client.delete(f"/api/agents/{AGENT_NAME}", headers=h)
    print(f"[INFO] delete agent: {r.status_code}")
    r = client.delete(f"/api/threads/{THREAD_ID}", headers=h)
    print(f"[INFO] delete thread: {r.status_code}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["forward", "deny", "timeout", "idem", "cleanup"], required=True)
    parser.add_argument("--fresh", action="store_true", help="forward 前删除重建 thread")
    args = parser.parse_args()
    with httpx.Client(base_url=BASE_URL, timeout=60, follow_redirects=True) as client:
        _login(client)
        if args.stage == "forward":
            _stage_forward(client, args.fresh)
        else:
            {"deny": _stage_deny, "timeout": _stage_timeout, "idem": _stage_idem, "cleanup": _stage_cleanup}[args.stage](client)
    return 0


if __name__ == "__main__":
    sys.exit(main())
