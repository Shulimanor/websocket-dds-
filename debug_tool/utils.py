"""工具函数 + 信号处理。"""

import asyncio
import json
import signal
import sys
from typing import Any

from debug_tool.logger import log_warn

# ---------------------------------------------------------------------------
# 异步 stdin
# ---------------------------------------------------------------------------


async def ainput(prompt: str = "") -> str:
    """异步读取 stdin 一行。"""
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    return await asyncio.get_running_loop().run_in_executor(None, sys.stdin.readline)


async def async_stdin_lines(shutdown: asyncio.Event) -> str | None:
    """非阻塞轮询 stdin，返回去尾换行的文本行；EOF 返回 None。"""
    while not shutdown.is_set():
        try:
            line = await asyncio.wait_for(ainput(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        if not line:
            return None
        text = line.rstrip("\n").rstrip("\r")
        if text:
            return text
    return None


# ---------------------------------------------------------------------------
# JSON / 消息格式化
# ---------------------------------------------------------------------------


def try_parse_json(text: str) -> Any:
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] in "{[ " and stripped[-1] in "}] ":
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def format_message(data: str | bytes, show_binary_hex: bool = False) -> str:
    if isinstance(data, bytes):
        if show_binary_hex:
            if len(data) <= 256:
                return f"[BIN {len(data)}B] {data.hex()}"
            return f"[BIN {len(data)}B] {data[:128].hex()}...({len(data)-128}B more)"
        return f"[BIN {len(data)}B]"
    obj = try_parse_json(data)
    if obj is not None:
        return f"\n{json.dumps(obj, indent=2, ensure_ascii=False)}"
    return data


# ---------------------------------------------------------------------------
# 信号处理
# ---------------------------------------------------------------------------

_shutdown_event: asyncio.Event | None = None


def _signal_handler(signum: int, _frame: Any) -> None:
    sig_name = signal.Signals(signum).name
    log_warn(f"收到 {sig_name}，正在优雅关闭...")
    if _shutdown_event is not None:
        _shutdown_event.set()


def setup_signal_handlers(shutdown_event: asyncio.Event) -> None:
    global _shutdown_event
    _shutdown_event = shutdown_event
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, lambda: shutdown_event.set())
        loop.add_signal_handler(signal.SIGTERM, lambda: shutdown_event.set())
    except (NotImplementedError, RuntimeError):
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
