#!/usr/bin/env python3
"""WebSocket / DDS 调试工具 —— 带 Web 面板。

启动：python run.py [--port 8765]
浏览器打开后，顶部下拉选择：
  - WebSocket：动态启动 Server / Client
  - DDS：动态启动 Publisher / Subscriber（基于 Cyclone DDS）
"""

import argparse
import asyncio

from debug_tool.logger import configure_logging, log_info
from debug_tool.utils import setup_signal_handlers
from debug_tool.hub import DebugHub

# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="WebSocket 调试工具 — 带 Web 面板",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                        # 默认 http://localhost:8765
  %(prog)s --port 9999            # 自定义端口
  %(prog)s --host 127.0.0.1       # 仅本机访问
  %(prog)s --no-color --debug     # 禁用颜色 + 调试日志
        """)
    p.add_argument("--host", default="0.0.0.0", help="面板监听地址，默认 0.0.0.0")
    p.add_argument("--port", type=int, default=8765, help="面板端口，默认 8765")
    p.add_argument("--debug", action="store_true", help="启用 DEBUG 日志")
    p.add_argument("--quiet", action="store_true", help="只显示错误和警告")
    p.add_argument("--no-color", action="store_true", help="禁用彩色输出")
    return p.parse_args(argv)



def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    configure_logging(args)

    shutdown_event = asyncio.Event()
    setup_signal_handlers(shutdown_event)

    hub = DebugHub(host=args.host, port=args.port, shutdown_event=shutdown_event)
    try:
        asyncio.run(hub.run())
    except KeyboardInterrupt:
        log_info("用户中断，退出。")


if __name__ == "__main__":
    main()

