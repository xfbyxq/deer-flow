#!/usr/bin/env python3
"""M0 冒烟 stub MCP server（S2/S3/S6 共用）。

提供 m0_echo / m0_sleep / m0_headers 三个工具，供 DeerFlow 员工 run 内调用：
- S3 工具可见性/命名冲突；S6 headers_from_context 注入（m0_headers 尽力回显收到的 HTTP headers）；
- S2 超时边界（m0_sleep 挂起不同秒数，观察 DeerFlow HTTP transport 的实际等待上限）。

运行：backend/.venv/bin/python docs/companion/m0-smoke/stub_mcp_server.py  （127.0.0.1:8765/mcp）
DeerFlow 容器内经 http://host.docker.internal:8765/mcp 访问（注册于 extensions_config.json m0-stub）。
"""

from __future__ import annotations

import asyncio
import logging

from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP(
    "m0-stub",
    host="0.0.0.0",  # 容器内经 host.docker.internal 访问，须绑非 loopback
    port=8765,
    streamable_http_path="/mcp",
    log_level="INFO",
)


def _headers(ctx: Context) -> dict:
    """防御式读取当前请求的 HTTP headers（不同 transport/SDK 版本可能不可用）。"""
    try:
        req = getattr(getattr(ctx, "request_context", None), "http_request", None)
        if req is not None:
            return {k: v for k, v in req.headers.items()}
    except Exception:
        pass
    return {}


@mcp.tool()
async def m0_echo(text: str, ctx: Context) -> dict:
    """回显文本并报告本次调用收到的 HTTP headers。"""
    return {"echo": text, "headers": _headers(ctx)}


@mcp.tool()
async def m0_sleep(seconds: float, ctx: Context) -> dict:
    """挂起指定秒数后返回（用于探测调用超时边界）。"""
    await asyncio.sleep(max(0.0, seconds))
    return {"slept": seconds}


@mcp.tool()
async def m0_headers(ctx: Context) -> dict:
    """报告本次调用收到的 HTTP headers。"""
    return {"headers": _headers(ctx)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(mcp.run_streamable_http_async())
