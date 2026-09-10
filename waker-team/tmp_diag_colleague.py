#!/usr/bin/env python3
"""一次性诊断脚本：验证 waker-team MCP 工具（query_group 查同事）在两种 run 下的行为。

对照实验：
  A) run 请求不带 config.context.secrets → 预期 MCP 拦截器 deny（缺 waker_identity）
  B) run 请求带 config.context.secrets.waker_identity=<caller> → 预期工具正常返回同事列表

用后即删。
"""

import json
import time
import urllib.request

BASE = "http://127.0.0.1:2026"

cookies = []
csrf = None
with open("/tmp/df_cookies.txt") as f:
    for line in f:
        line = line.rstrip("\n")
        if not line.strip():
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        elif line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            cookies.append(f"{parts[5]}={parts[6]}")
            if parts[5] == "csrf_token":
                csrf = parts[6]
COOKIE = "; ".join(cookies)
CSRF = csrf


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Cookie": COOKIE}
    if method in ("POST", "PUT", "PATCH", "DELETE") and CSRF:
        headers["X-CSRF-Token"] = CSRF
    r = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(r, timeout=60) as resp:
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else None)


def run_scenario(label: str, secrets: dict | None):
    print(f"\n================ {label} ================")
    tid = f"diag-colleague-{int(time.time())}-{label[:1]}"
    req("POST", "/api/threads", {"thread_id": tid})

    config: dict = {"configurable": {"agent_name": "zww"}}
    if secrets is not None:
        config["context"] = {"secrets": secrets}

    st, run = req(
        "POST",
        f"/api/threads/{tid}/runs",
        {
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": "请调用工具查询团队成员（你的同事）列表，然后把结果告诉我。",
                    }
                ]
            },
            "config": config,
        },
    )
    rid = run.get("run_id")
    print("run_id:", rid)

    for i in range(40):
        time.sleep(3)
        st, r = req("GET", f"/api/threads/{tid}/runs/{rid}")
        status = r.get("status")
        if status in ("success", "error", "timeout", "interrupted"):
            print("terminal status:", status)
            break
    else:
        print("still running after 120s")
        return

    st, state = req("GET", f"/api/threads/{tid}/state")
    msgs = (state.get("values") or {}).get("messages") or []
    for m in msgs:
        mtype = m.get("type")
        if mtype == "ai":
            tcs = m.get("tool_calls") or []
            content = m.get("content")
            if isinstance(content, list):
                content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
            if tcs:
                for tc in tcs:
                    print(f"[ai tool_call] {tc.get('name')} args={json.dumps(tc.get('args'), ensure_ascii=False)[:200]}")
            if content and content.strip() and not (m.get("additional_kwargs") or {}).get("hide_from_ui"):
                print(f"[ai text] {str(content)[:400]}")
        elif mtype == "tool":
            content = m.get("content")
            if isinstance(content, list):
                content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
            print(f"[tool result] {str(content)[:500]}")


def main():
    # A: 现状（无 secrets）——预期被 deny
    run_scenario("A-无secrets", None)
    # B: 带 waker_identity —— 预期工具正常
    run_scenario("B-带waker_identity", {"waker_identity": "zww"})


if __name__ == "__main__":
    main()
