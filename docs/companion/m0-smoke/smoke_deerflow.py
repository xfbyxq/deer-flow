#!/usr/bin/env python3
"""M0 冒烟脚本（v0.1，S1 + S7 先行版）—— 见 2026-09-09-waker-team-p0-plan.md。

运行（凭据仅经环境变量注入，不落盘不进历史参数）:
    DEERFLOW_SMOKE_EMAIL=<邮箱> DEERFLOW_SMOKE_PASSWORD=<密码> \\
        backend/.venv/bin/python docs/companion/m0-smoke/smoke_deerflow.py [--expect-agents a,b,c]

覆盖:
    S1 服务账号程序化登录: providers -> login/local -> me -> CSRF 负/正向探测 ->
       agents CRUD 回路 -> threads 创建（角色/权限观察）
    S7 枚举与名单: GET /api/agents 全量名单（与期望名单比对输出）、
       models/skills 枚举端点候选探测（404 记录为"需源码确认"）

说明:
    - login/local 免 CSRF、无 Origin 的服务端客户端放行（csrf_middleware._AUTH_EXEMPT_PATHS）；
    - 其他 state-changing 请求需 double-submit: cookie csrf_token == header X-CSRF-Token；
    - agents 创建仅 name 必填（agents.py AgentCreateRequest）。
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

BASE_URL = os.environ.get("DEERFLOW_SMOKE_BASE_URL", "http://127.0.0.1:2026")
LOGIN_PATH = "/api/v1/auth/login/local"
ME_PATH = "/api/v1/auth/me"
PROVIDERS_PATH = "/api/v1/auth/providers"
AGENTS_PATH = "/api/agents"
THREADS_PATH = "/api/threads"
CSRF_HEADER = "X-CSRF-Token"
CSRF_COOKIE = "csrf_token"
SMOKE_PREFIX = "m0-smoke-"

results: list[dict] = []


def record(step: str, ok: bool, detail: str) -> None:
    results.append({"step": step, "ok": ok, "detail": detail})
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {step}: {detail}")


def csrf_headers(client: httpx.Client) -> dict[str, str]:
    """带 cookie jar 中 csrf_token 的写请求头（double-submit 配对）。"""
    token = client.cookies.get(CSRF_COOKIE)
    if not token:
        return {}
    return {CSRF_HEADER: token}


def step_s1(client: httpx.Client, email: str, password: str) -> None:
    """S1 服务账号程序化登录 + CSRF 行为 + agents/threads 权限探测。"""
    # 1) providers（公开端点）
    try:
        r = client.get(PROVIDERS_PATH)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        providers = [p.get("id") or p.get("name") or str(p) for p in body] if isinstance(body, list) else body
        record("S1.1 providers 公开可探测", r.status_code == 200, f"status={r.status_code} body={providers!r}")
    except httpx.HTTPError as exc:
        record("S1.1 providers 公开可探测", False, f"{type(exc).__name__}: {exc}")
        return

    # 2) 登录（OAuth2 表单）
    try:
        r = client.post(LOGIN_PATH, data={"username": email, "password": password, "remember_me": "true"})
        cookies = dict(client.cookies)
        csrf_ok = CSRF_COOKIE in cookies
        detail = f"status={r.status_code} cookies={{access:{'access_token' in cookies}, csrf:{csrf_ok}}}"
        if r.status_code == 200 and csrf_ok:
            try:
                detail += f" body={r.json()}"
            except ValueError:
                pass
            record("S1.2 login/local 成功且下发 csrf cookie", True, detail)
        else:
            record("S1.2 login/local 成功且下发 csrf cookie", False, f"{detail} body={r.text[:300]}")
            return
    except httpx.HTTPError as exc:
        record("S1.2 login/local 成功且下发 csrf cookie", False, f"{type(exc).__name__}: {exc}")
        return

    # 3) me：身份/角色/needs_setup
    try:
        r = client.get(ME_PATH)
        me = r.json() if r.status_code == 200 else {}
        detail = f"status={r.status_code}"
        if r.status_code == 200:
            detail += f" id={me.get('id')} email={me.get('email')} role={me.get('system_role') or me.get('role')} needs_setup={me.get('needs_setup')}"
        record("S1.3 GET /api/v1/auth/me", r.status_code == 200, detail)
    except httpx.HTTPError as exc:
        record("S1.3 GET /api/v1/auth/me", False, f"{type(exc).__name__}: {exc}")

    # 4) CSRF 负向：不带 X-CSRF-Token 的 POST 应被拒
    probe_name = f"{SMOKE_PREFIX}csrfneg"
    try:
        r = client.post(f"{AGENTS_PATH}/{probe_name}", json={"name": probe_name, "description": "csrf-negative-probe"})
        record("S1.4 无 CSRF 头写请求被拒(403)", r.status_code == 403,
               f"status={r.status_code}（非 403 需核查豁免面）body={r.text[:200]}")
    except httpx.HTTPError as exc:
        record("S1.4 无 CSRF 头写请求被拒(403)", False, f"{type(exc).__name__}: {exc}")

    # 5) agents CRUD 回路（带 CSRF）
    name = f"{SMOKE_PREFIX}{os.getpid()}"
    try:
        r = client.post(AGENTS_PATH, json={"name": name, "description": "m0-smoke-s1", "soul": "# m0 smoke"}, headers=csrf_headers(client))
        record("S1.5a agents 创建(带CSRF)", r.status_code in (200, 201), f"status={r.status_code} name={name} body={r.text[:200]}")
        if r.status_code in (200, 201):
            r2 = client.get(f"{AGENTS_PATH}/{name}")
            record("S1.5b agents 读回", r2.status_code == 200, f"status={r2.status_code}")
            r3 = client.delete(f"{AGENTS_PATH}/{name}", headers=csrf_headers(client))
            record("S1.5c agents 删除(带CSRF)", r3.status_code in (200, 204), f"status={r3.status_code}")
    except httpx.HTTPError as exc:
        record("S1.5 agents CRUD 回路", False, f"{type(exc).__name__}: {exc}")

    # 6) threads 创建（POST {} 全默认；无 DELETE 端点时记录需手动清理）
    try:
        r = client.post(THREADS_PATH, json={}, headers=csrf_headers(client))
        tid = (r.json() or {}).get("thread_id") if r.status_code in (200, 201) else None
        record("S1.6a threads 创建(带CSRF)", r.status_code in (200, 201) and bool(tid),
               f"status={r.status_code} thread_id={tid}")
        if tid:
            r2 = client.get(f"{THREADS_PATH}/{tid}")
            record("S1.6b threads 读回", r2.status_code == 200, f"status={r2.status_code}")
            r3 = client.delete(f"{THREADS_PATH}/{tid}", headers=csrf_headers(client))
            record("S1.6c threads 删除(若端点存在)", r3.status_code in (200, 204, 404, 405),
                   f"status={r3.status_code}（404/405=无删除端点，需手动清理该 thread）")
    except httpx.HTTPError as exc:
        record("S1.6 threads 创建回路", False, f"{type(exc).__name__}: {exc}")


def _extract_agent_names(data) -> tuple[list[str], str]:
    """兼容实际响应形状：dict{"agents": [dict]} 或 list[dict]。"""
    if isinstance(data, dict) and isinstance(data.get("agents"), list):
        return [a.get("name") for a in data["agents"] if isinstance(a, dict)], "dict{agents:[...]}"
    if isinstance(data, list):
        return [a.get("name") for a in data if isinstance(a, dict)], "list"
    return [], f"unexpected shape {type(data).__name__}"


def step_s7(client: httpx.Client, expect_agents: list[str]) -> None:
    """S7 现有员工名单可见性 + 枚举端点候选探测。"""
    # 1) agents 全量名单
    try:
        r = client.get(AGENTS_PATH)
        data = r.json() if r.status_code == 200 else {}
        names, shape = _extract_agent_names(data)
        names = sorted(names)
        detail = f"status={r.status_code} shape={shape} count={len(names)} names={names}"
        if expect_agents:
            missing = [n for n in expect_agents if n not in names]
            detail += f" | 期望名单缺失={missing if missing else '无'}"
        record("S7.1 agents_api 全量名单", r.status_code == 200, detail)
    except httpx.HTTPError as exc:
        record("S7.1 agents_api 全量名单", False, f"{type(exc).__name__}: {exc}")

    # 2) 枚举端点候选探测（200=锁定；404=不存在；403=存在但无权限——都记录）
    for path in ("/api/models", "/api/models/list", "/api/skills", "/api/tool-groups", "/api/tools/groups"):
        try:
            r = client.get(path)
            hit = r.status_code in (200, 403)
            note = "存在" if hit else "不存在/未知"
            print(f"[INFO] S7.2 枚举探测 {path} -> status={r.status_code} ({note})")
            if hit:
                record(f"S7.2 枚举端点 {path}", True, f"status={r.status_code} body={r.text[:150]}")
        except httpx.HTTPError as exc:
            print(f"[INFO] S7.2 枚举探测 {path} -> {type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Waker Team M0 冒烟（S1/S7）")
    parser.add_argument("--expect-agents", help="期望在 agents_api 名单中的员工名（逗号分隔）")
    args = parser.parse_args()

    email = os.environ.get("DEERFLOW_SMOKE_EMAIL")
    password = os.environ.get("DEERFLOW_SMOKE_PASSWORD")
    if not email or not password:
        print("缺少凭据：请设置环境变量 DEERFLOW_SMOKE_EMAIL / DEERFLOW_SMOKE_PASSWORD", file=sys.stderr)
        return 2

    expect = [n.strip() for n in args.expect_agents.split(",") if n.strip()] if args.expect_agents else []
    print(f"base_url = {BASE_URL}；期望员工名单 = {expect or '（未指定，仅输出实际名单）'}")

    with httpx.Client(base_url=BASE_URL, timeout=30, follow_redirects=True) as client:
        step_s1(client, email, password)
        step_s7(client, expect)

    ok_count = sum(1 for r in results if r["ok"])
    print(f"\n== 汇总: {ok_count}/{len(results)} PASS ==")
    for r in results:
        if not r["ok"]:
            print(f"  FAIL {r['step']}: {r['detail']}")
    print("回填点: S1 -> 架构 §4.6/附录 A（CSRF 双提交、角色权限、会话行为）; S7 -> §4.1.1/§12 #2（名单与枚举端点）")
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
