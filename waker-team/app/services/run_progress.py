"""运行进度快照解析 — 从 DeerFlow thread state 提取执行步骤/当前动作。

供多处复用：
- 会话回复等待期进度（chat_reply.get_progress）；
- 成员任务详情下钻（GET /tasks/{id}/progress，群协作运行状态条）。

本模块是纯函数集合，不做 IO，便于单测。
"""

from __future__ import annotations


def strip_think(text: str) -> str | None:
    """剔除模型思考段（<think>…</think>），只保留正式回复。

    - 含闭合 ``</think>``：取后半段（思考内容不得写入对话）
    - 含未闭合 ``<think>``：整条不可用，返回 None
    - 剔除后为空：返回 None
    """
    if "</think>" in text:
        tail = text.split("</think>", 1)[1].strip()
        return tail or None
    if "<think>" in text:
        return None
    return text.strip() or None


def coerce_content_text(content) -> str:
    """将消息 content（str 或 parts 列表）归一为纯文本."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")))
            else:
                parts.append(str(part))
        return "".join(parts)
    return str(content)


def args_summary(args) -> str:
    """工具参数摘要：优先取语义字段（query/instruction/…），否则整体截断."""
    if not isinstance(args, dict) or not args:
        return ""
    for key in ("query", "instruction", "text", "url", "target", "group_id"):
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:60]
    return str(args)[:60]


def extract_progress(state: dict, run_id: str | None = None) -> tuple[list[dict], dict]:
    """从 thread state 提取当前轮的执行步骤与当前动作。

    - steps: 工具调用链 [{"name", "detail", "done", "result"}]
    - current: 当前动作 {"kind": "starting"|"tool"|"thinking"|"unknown", "name"?, "detail"?}
    """
    messages = ((state.get("values") or {}).get("messages")) or []

    # 取最近一条可见 human 消息之后的全部消息（当前轮）
    tail: list[dict] = []
    for m in reversed(messages):
        if m.get("type") == "human" and not (m.get("additional_kwargs") or {}).get(
            "hide_from_ui"
        ):
            break
        tail.append(m)
    tail.reverse()

    steps: list[dict] = []
    for m in tail:
        mtype = m.get("type")
        if mtype == "ai":
            for tc in m.get("tool_calls") or []:
                steps.append(
                    {
                        "name": tc.get("name") or "tool",
                        "detail": args_summary(tc.get("args")),
                        "done": False,
                        "result": None,
                    }
                )
        elif mtype == "tool":
            name = m.get("name") or "tool"
            result_text = strip_think(coerce_content_text(m.get("content"))) or ""
            result = result_text[:80] + ("…" if len(result_text) > 80 else "")
            for s in reversed(steps):
                if not s["done"] and s["name"] == name:
                    s["done"] = True
                    s["result"] = result
                    break
            else:
                steps.append({"name": name, "detail": "", "done": True, "result": result})

    pending = [s for s in steps if not s["done"]]
    if pending:
        current = {"kind": "tool", "name": pending[-1]["name"], "detail": pending[-1]["detail"]}
    elif not tail:
        current = {"kind": "starting", "detail": "正在启动…"}
    else:
        last = tail[-1]
        if last.get("type") == "ai" and not (last.get("tool_calls") or []):
            current = {"kind": "thinking", "detail": "正在生成回复…"}
        elif last.get("type") == "tool":
            current = {"kind": "thinking", "detail": "正在整理信息…"}
        else:
            current = {"kind": "thinking", "detail": "正在思考…"}
    return steps, current


def latest_visible_output(state: dict) -> str | None:
    """当前轮最后一条可见 AI 正文（剔除思考段），供详情抽屉展示「最近输出」."""
    messages = ((state.get("values") or {}).get("messages")) or []
    for m in reversed(messages):
        if m.get("type") != "ai":
            continue
        extra = m.get("additional_kwargs") or {}
        if extra.get("hide_from_ui"):
            continue
        text = strip_think(coerce_content_text(m.get("content")))
        if text:
            return text
    return None
