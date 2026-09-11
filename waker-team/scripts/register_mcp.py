#!/usr/bin/env python3
"""将 waker-team MCP server 注册到 DeerFlow（经 Gateway MCP 配置 API）。

用法:
    python scripts/register_mcp.py [--port 8765] [--unregister]
    python scripts/register_mcp.py --email admin@example.com   # 用管理员账号注册

与旧版直接改写 deer-flow/extensions_config.json 不同，本脚本通过 DeerFlow
Gateway 的 REST API 操作（/api/mcp/config*，需管理员账号）：

- 位置无关：不再假设 waker-team 位于 deer-flow 目录之下；
- 服务端写锁 + schema 校验 + 密钥保护；
- 注册/注销后 Gateway 自动热重载，无需重启；
- 幂等：重复注册→更新；重复注销→静默成功。

凭据解析（DEERFLOW_BASE_URL 始终取自 waker-team/.env 或环境变量）：
    邮箱: --email > .env 的 SERVICE_EMAIL
    密码: 指定 --email 时取 DEERFLOW_ADMIN_PASSWORD 环境变量，否则交互输入；
          未指定时取 .env 的 SERVICE_PASSWORD
注意：执行注册/注销的账号必须是 DeerFlow 管理员（system_role=admin）；
      waker-team 服务账号通常不是管理员，此时请用 --email 指定管理员账号。
"""

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

# waker-team 根目录（相对脚本自身定位，与仓库外部位置无关）
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.config import Settings  # noqa: E402
from app.deerflow.client import DeerFlowClient  # noqa: E402
from app.deerflow.errors import DeerFlowError, McpServerNotFoundError  # noqa: E402

# .env 相对 waker-team 根目录定位，不依赖当前工作目录
ENV_FILE = REPO_ROOT / ".env"
SERVER_NAME = "waker-team"


def _server_config(port: int) -> dict:
    """MCP server 配置体（与旧版写入 extensions_config.json 的结构一致）。

    - headers_from_context: DeerFlow 将 run 的 context.secrets.waker_identity
      映射为 X-Waker-Caller 头；on_missing=deny 缺失时 fail-closed 拒绝调用；
    - 不设 tool_call_timeout（该字段对 HTTP transport 无效）。
    """
    return {
        "enabled": True,
        "type": "http",
        "url": f"http://host.docker.internal:{port}/mcp",
        "headers_from_context": {
            "headers": {"X-Waker-Caller": "waker_identity"},
            "on_missing": "deny",
        },
    }


def _resolve_password(email_arg: str | None, settings: Settings) -> str | None:
    """解析登录密码。

    - 显式指定 --email（管理员账号）：DEERFLOW_ADMIN_PASSWORD 环境变量优先，
      否则交互式输入（不落盘、不进 shell 历史）；
    - 默认（.env 服务账号）：使用 SERVICE_PASSWORD。
    """
    if email_arg:
        env_password = os.environ.get("DEERFLOW_ADMIN_PASSWORD")
        if env_password:
            return env_password
        return getpass.getpass(f"DeerFlow 管理员密码（{email_arg}）: ")
    return settings.service_password or None


async def _connect(
    email: str | None = None, password: str | None = None
) -> DeerFlowClient:
    """按解析后的凭据登录 DeerFlow Gateway."""
    settings = Settings(_env_file=str(ENV_FILE))
    resolved_email = email or settings.service_email
    resolved_password = (
        password if password is not None else _resolve_password(email, settings)
    )
    if not resolved_email or not resolved_password:
        raise SystemExit(
            "缺少登录凭据：请配置 waker-team/.env 的 SERVICE_EMAIL/SERVICE_PASSWORD，"
            "或用 --email 指定 DeerFlow 管理员账号"
        )
    client = DeerFlowClient(settings.deerflow_base_url)
    try:
        await client.login(resolved_email, resolved_password)
    except Exception:
        await client.close()
        raise
    return client


async def register(
    port: int = 8765, *, email: str | None = None, password: str | None = None
) -> None:
    """注册（或更新）waker-team MCP server（幂等）."""
    client = await _connect(email=email, password=password)
    try:
        servers = await client.get_mcp_config()
        config = _server_config(port)
        if SERVER_NAME in servers:
            await client.update_mcp_server(SERVER_NAME, config)
            print(f"✓ 已更新 {SERVER_NAME} MCP server（端口 {port}）")
        else:
            await client.add_mcp_server(SERVER_NAME, config)
            print(f"✓ 已注册 {SERVER_NAME} MCP server（端口 {port}）")
        print(f"  URL: http://host.docker.internal:{port}/mcp")
        print("  已热重载生效（无需重启 DeerFlow Gateway）")
    finally:
        await client.close()


async def unregister(
    *, email: str | None = None, password: str | None = None
) -> None:
    """注销 waker-team MCP server（幂等：未注册时静默成功）."""
    client = await _connect(email=email, password=password)
    try:
        try:
            await client.delete_mcp_server(SERVER_NAME)
            print(f"✓ 已注销 {SERVER_NAME} MCP server")
        except McpServerNotFoundError:
            print(f"{SERVER_NAME} 未注册，无需注销")
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register/unregister waker-team MCP server in DeerFlow"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="MCP server port (default: 8765)",
    )
    parser.add_argument(
        "--unregister",
        action="store_true",
        help="Remove waker-team from DeerFlow MCP configuration",
    )
    parser.add_argument(
        "--email",
        help=(
            "DeerFlow admin account (default: SERVICE_EMAIL from .env; "
            "password via DEERFLOW_ADMIN_PASSWORD env or interactive prompt)"
        ),
    )
    args = parser.parse_args()

    try:
        if args.unregister:
            asyncio.run(unregister(email=args.email))
        else:
            asyncio.run(register(args.port, email=args.email))
    except DeerFlowError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if "403" in str(exc):
            print(
                "提示：当前账号没有管理员权限；请用 --email 指定 DeerFlow 管理员账号重试",
                file=sys.stderr,
            )
        sys.exit(1)


if __name__ == "__main__":
    main()
