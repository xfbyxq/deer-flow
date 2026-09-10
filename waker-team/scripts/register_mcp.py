#!/usr/bin/env python3
"""将 waker-team MCP server 注册到 DeerFlow 的 extensions_config.json。

用法:
    python scripts/register_mcp.py [--port 8765] [--unregister]

注册后需重启 DeerFlow Gateway 或 touch extensions_config.json 触发热加载。
"""

import argparse
import json
import sys
from pathlib import Path

# deer-flow/ 根目录（waker-team 的上级目录的上级）
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EXTENSIONS_CONFIG = REPO_ROOT / "extensions_config.json"


def register(port: int = 8765) -> None:
    """在 extensions_config.json 中添加 waker-team MCP server 配置。"""
    if not EXTENSIONS_CONFIG.exists():
        print(f"Error: {EXTENSIONS_CONFIG} not found", file=sys.stderr)
        sys.exit(1)

    config = json.loads(EXTENSIONS_CONFIG.read_text(encoding="utf-8"))

    # 确保 mcpServers 键存在
    if "mcpServers" not in config:
        config["mcpServers"] = {}

    # 添加 waker-team server 配置
    # - headers_from_context: DeerFlow 将 context.secrets.waker_identity 映射为 X-Waker-Caller header
    # - on_missing: deny: 缺失 header 时拒绝工具调用
    # - 不设 tool_call_timeout（M0 S2 确认该字段对 HTTP transport 无效）
    config["mcpServers"]["waker-team"] = {
        "enabled": True,
        "type": "http",
        "url": f"http://host.docker.internal:{port}/mcp",
        "headers_from_context": {
            "headers": {"X-Waker-Caller": "waker_identity"},
            "on_missing": "deny",
        },
    }

    EXTENSIONS_CONFIG.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"✓ Registered waker-team MCP server at port {port}")
    print(f"  URL: http://host.docker.internal:{port}/mcp")
    print("  Note: touch extensions_config.json or restart Gateway to reload")


def unregister() -> None:
    """从 extensions_config.json 中移除 waker-team MCP server 配置。"""
    if not EXTENSIONS_CONFIG.exists():
        print(f"Error: {EXTENSIONS_CONFIG} not found", file=sys.stderr)
        sys.exit(1)

    config = json.loads(EXTENSIONS_CONFIG.read_text(encoding="utf-8"))
    removed = config.get("mcpServers", {}).pop("waker-team", None)

    EXTENSIONS_CONFIG.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if removed:
        print("✓ Unregistered waker-team MCP server")
    else:
        print("waker-team MCP server was not registered")


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
        help="Remove waker-team from extensions_config.json",
    )
    args = parser.parse_args()

    if args.unregister:
        unregister()
    else:
        register(args.port)


if __name__ == "__main__":
    main()
