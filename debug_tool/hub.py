"""WebSocket 调试中枢 —— 多面板 + 动态子服务器 / 子客户端。

支持多个浏览器标签页同时连接。一个可做 WS Server，另一个做 DDS Pub/Sub。
面板断开后自动重连并恢复完整状态。
"""

from __future__ import annotations

import asyncio
import base64
import json
import ssl
import sys
import time
import uuid
from pathlib import Path
from typing import Any

try:
    import websockets
    from websockets.asyncio.server import ServerConnection
    from websockets.http11 import Headers, Response
except ImportError:
    print("缺少依赖: websockets。请安装: pip install websockets")
    sys.exit(1)

from debug_tool.logger import (
    log_debug, log_info, log_send, log_recv, log_stat, log_warn, log_error,
)
from debug_tool.utils import format_message, try_parse_json, async_stdin_lines
from debug_tool.dds import DdsEngine

def _resolve_frontend_path() -> Path:
    """定位前端 index.html。

    源码运行：debug_tool/ 的上一级目录。
    PyInstaller 打包（--add-data 'index.html;.' 打进包内）：运行时解压的临时目录。
    """
    candidates = [
        Path(__file__).resolve().parent.parent / "index.html",
        Path(getattr(sys, "_MEIPASS", "") or ".") / "index.html",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


_HTML_PATH = _resolve_frontend_path()
try:
    DEBUG_PAGE = _HTML_PATH.read_text(encoding="utf-8")
except FileNotFoundError:
    print(f"错误: 找不到前端文件 {_HTML_PATH}")
    sys.exit(1)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _connect_candidates(url: str) -> list[str]:
    """生成连接候选地址。

    Windows 上 'localhost' 通常先解析到 IPv6 (::1)，而很多服务只监听 IPv4
    (127.0.0.1)，导致 ws://localhost 连不上、ws://127.0.0.1 却可以。
    这里对 localhost 生成 [IPv4, 原样] 两个候选，逐个尝试。
    """
    try:
        from urllib.parse import urlsplit, urlunsplit
        u = urlsplit(url)
    except Exception:
        return [url]
    if not u.scheme or not u.hostname:
        return [url]
    if u.hostname.lower() != "localhost":
        return [url]
    netloc = "127.0.0.1"
    if u.username:
        netloc = u.username + (":" + u.password if u.password else "") + "@" + netloc
    if u.port:
        netloc += f":{u.port}"
    alt = urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment))
    # IPv4 在前（本机工具最常见），原样兜底（兼容仅 IPv6 服务）
    return [alt, url]


class DebugHub:
    """WebSocket 调试中枢（支持多面板）。

    多个浏览器标签页可同时连接，每个面板消息独立隔离。
    面板断开自动重连，重连后自动恢复完整状态。
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8765,
                 shutdown_event: asyncio.Event | None = None) -> None:
        self.host = host
        self.port = port
        self._shutdown = shutdown_event or asyncio.Event()

        # 管理服务器
        self._mgr_server: websockets.Server | None = None

        # 多个面板连接 (panel_id -> ws)
        self._panels: dict[str, ServerConnection] = {}

        # 动态测试服务器
        self._test_server: websockets.Server | None = None
        self._test_server_broadcast: bool = False
        self._test_server_host: str = ""
        self._test_server_port: int = 0
        self._test_clients: set[ServerConnection] = set()
        self._server_owner: str | None = None

        # 动态客户端
        self._client_ws: websockets.ClientConnection | None = None
        self._client_task: asyncio.Task | None = None
        self._client_owner: str | None = None

        # DDS 引擎
        self._dds = DdsEngine(domain=0, push_log=self._push_log, push_status=self._push_status)

        # 统计
        self._msg_sent = 0
        self._msg_recv = 0

    # =========================================================================
    # 面板 → 推送
    # =========================================================================

    async def _push(self, data: dict, panel_id: str | None = None) -> None:
        if not self._panels:
            return
        dead: list[str] = []
        msg = json.dumps(data, ensure_ascii=False)
        for pid, ws in list(self._panels.items()):
            if panel_id is not None and pid != panel_id:
                continue
            try:
                await ws.send(msg)
            except websockets.ConnectionClosed:
                dead.append(pid)
        for pid in dead:
            self._panels.pop(pid, None)
            log_debug(f"面板 {pid} 已断开（广播时清理）")

    async def _push_log(self, dir_: str, peer: str, text: str, panel_id: str | None = None) -> None:
        await self._push({"type": "log", "dir": dir_, "peer": peer,
                           "time": time.strftime("%H:%M:%S"), "text": text}, panel_id=panel_id)

    async def _push_status(self, state: str, panel_id: str | None = None, **kw: Any) -> None:
        await self._push({"type": "status", "state": state, **kw}, panel_id=panel_id)

    # =========================================================================
    # 主入口
    # =========================================================================

    async def run(self) -> None:
        self._mgr_server = await websockets.serve(
            self._mgr_handler, self.host, self.port,
            process_request=self._mgr_process_request,
        )

        display = "localhost" if self.host in ("0.0.0.0", "::") else self.host
        log_info(f"调试面板: http://{display}:{self.port}")
        log_info("浏览器打开上述地址，在面板中启动 Server 或连接 Client")

        stdin_task = asyncio.create_task(self._stdin_loop())
        await asyncio.wait([stdin_task, asyncio.create_task(self._shutdown.wait())],
                           return_when=asyncio.FIRST_COMPLETED)

        log_info("正在关闭...")
        await self._stop_test_server()
        await self._stop_client()
        await self._dds.stop()
        if self._mgr_server:
            self._mgr_server.close()
            await self._mgr_server.wait_closed()
        stdin_task.cancel()
        try:
            await stdin_task
        except asyncio.CancelledError:
            pass
        log_stat(f"已关闭 — 发送 {self._msg_sent} / 接收 {self._msg_recv}")

    # =========================================================================
    # 管理服务器路由
    # =========================================================================

    def _mgr_process_request(self, _conn: Any, request: Any) -> Response | None:
        key = request.headers.get("sec-websocket-key")
        if key is not None:
            return None if self._is_valid_key(key) else Response(
                400, "Bad Request",
                Headers({"Content-Type": "text/plain; charset=utf-8"}),
                b"Invalid Sec-WebSocket-Key\n")
        html = DEBUG_PAGE.encode("utf-8")
        return Response(200, "OK",
            Headers({"Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(html))}), html)

    @staticmethod
    def _is_valid_key(key: str) -> bool:
        try:
            return len(base64.b64decode(key, validate=True)) == 16
        except (ValueError, TypeError):
            return False

    async def _mgr_handler(self, ws: ServerConnection) -> None:
        await self._handle_panel(ws)

    # =========================================================================
    # 面板控制通道
    # =========================================================================

    async def _handle_panel(self, ws: ServerConnection) -> None:
        pid = _uid()
        self._panels[pid] = ws
        log_info(f"面板 {pid} 已连接: {ws.remote_address} (当前 {len(self._panels)} 个面板)")
        await self._push_status("connected", panel_id=pid)

        # 推送当前完整状态，让刷新/重连后的前端恢复 UI（只推本面板拥有的服务）
        if self._test_server is not None and self._server_owner == pid:
            display = "localhost" if self._test_server_host in ("0.0.0.0", "::") else self._test_server_host
            await self._push_status("server_running", panel_id=pid,
                host=display, port=self._test_server_port,
                broadcast=self._test_server_broadcast,
                url=f"ws://{display}:{self._test_server_port}")
        if self._client_ws is not None and self._client_task is not None and not self._client_task.done() and self._client_owner == pid:
            await self._push_status("client_connected", panel_id=pid)
        if self._dds._pub_active and self._dds._pub_owner == pid:
            await self._push_status("pub_on", panel_id=pid, domain=self._dds.domain, topic=self._dds._topic_name)
        if self._dds._sub_active and self._dds._sub_owner == pid:
            await self._push_status("sub_on", panel_id=pid, domain=self._dds.domain, topic=self._dds._topic_name)

        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    msg = msg.decode("utf-8")
                await self._on_panel_cmd(msg, pid)
        except websockets.ConnectionClosed:
            pass
        except Exception as exc:
            log_error(f"面板 {pid} 异常: {exc}")
        finally:
            self._panels.pop(pid, None)
            # 清理本面板拥有的服务所有权
            if self._server_owner == pid:
                if self._test_server is not None:
                    self._test_server.close()
                    await self._test_server.wait_closed()
                    self._test_server = None
                self._test_clients.clear()
                self._server_owner = None
            if self._client_owner == pid:
                c = self._client_ws
                self._client_ws = None
                if c:
                    try: await c.close()
                    except Exception: pass
                t_ = self._client_task
                if t_ and not t_.done():
                    t_.cancel()
                    try: await t_
                    except asyncio.CancelledError: pass
                self._client_task = None
                self._client_owner = None
            if self._dds._pub_owner == pid:
                self._dds._pub_active = False
                self._dds._pub_owner = None
            if self._dds._sub_owner == pid:
                self._dds._sub_active = False
                self._dds._shutdown.set()
                if self._dds._sub_task and not self._dds._sub_task.done():
                    self._dds._sub_task.cancel()
                    try: await self._dds._sub_task
                    except asyncio.CancelledError: pass
                self._dds._sub_task = None
                self._dds._sub_owner = None
            log_info(f"面板 {pid} 已断开 (剩余 {len(self._panels)} 个面板)")

    async def _on_panel_cmd(self, msg: str, pid: str) -> None:
        obj = try_parse_json(msg)
        if not isinstance(obj, dict):
            return
        cmd = obj.get("cmd", "")
        try:
            if cmd == "start_server":
                self._server_owner = pid
                await self._start_test_server(
                    host=obj.get("host", "0.0.0.0"),
                    port=int(obj.get("port", 8765)),
                    broadcast=bool(obj.get("broadcast", False)),
                    panel_id=pid)
            elif cmd == "stop_server":
                await self._stop_test_server(panel_id=pid)
                await self._push_status("server_stopped", panel_id=pid)
            elif cmd == "start_client":
                self._client_owner = pid
                await self._start_client(
                    url=obj.get("url", ""),
                    timeout=float(obj.get("timeout", 10)),
                    binary=bool(obj.get("binary", False)),
                    no_verify=bool(obj.get("no_verify", False)),
                    ssl_cert=obj.get("ssl_cert") or None,
                    ssl_key=obj.get("ssl_key") or None,
                    no_ping=bool(obj.get("no_ping", False)),
                    panel_id=pid)
            elif cmd == "stop_client":
                await self._stop_client(panel_id=pid)
                await self._push_status("client_disconnected", panel_id=pid)
            elif cmd == "start_pub":
                domain = int(obj.get("domain", 0))
                topic = obj.get("topic", "DdsDebug")
                self._dds.domain = domain
                self._dds._topic_name = topic
                await self._dds.start_pub(panel_id=pid)
                if self._dds._pub_active:
                    await self._push_status("pub_on", panel_id=pid, domain=domain, topic=topic)
                else:
                    await self._push_status("error", panel_id=pid, info="DDS Publisher 启动失败，请确认 Cyclone DDS 已安装")
            elif cmd == "stop_pub":
                await self._dds.stop_pub()
                await self._push_status("pub_off", panel_id=pid)
            elif cmd == "start_sub":
                domain = int(obj.get("domain", 0))
                topic = obj.get("topic", "DdsDebug")
                self._dds.domain = domain
                self._dds._topic_name = topic
                await self._dds.start_sub(panel_id=pid)
                if self._dds._sub_active:
                    await self._push_status("sub_on", panel_id=pid, domain=domain, topic=topic)
                else:
                    await self._push_status("error", panel_id=pid, info="DDS Subscriber 启动失败，请确认 Cyclone DDS 已安装")
            elif cmd == "stop_sub":
                await self._dds.stop_sub()
                await self._push_status("sub_off", panel_id=pid)
            elif cmd == "dds_send":
                await self._dds.send(obj.get("text", ""), panel_id=pid)
            elif cmd == "send":
                await self._send_to_target(obj.get("text", ""), bool(obj.get("binary", False)), panel_id=pid)
        except Exception as exc:
            import traceback
            log_error(f"[面板命令失败] {cmd}: {exc}\n{traceback.format_exc()}")
            await self._push_status("error", panel_id=pid, info=f"{cmd} 失败: {exc}")
            await self._push_log("error", "", f"{cmd} 失败: {exc}", panel_id=pid)
            await self._push_log("error", "", traceback.format_exc(), panel_id=pid)

    # =========================================================================
    # 动态测试服务器
    # =========================================================================

    async def _start_test_server(self, host: str, port: int, broadcast: bool, panel_id: str | None = None) -> None:
        await self._stop_test_server(panel_id=panel_id)
        self._test_server_broadcast = broadcast
        self._test_server_host = host
        self._test_server_port = port
        self._test_server = await websockets.serve(self._test_server_handler, host, port)
        display = "localhost" if host in ("0.0.0.0", "::") else host
        tag = "广播" if broadcast else "Echo"
        log_info(f"[Server] 已启动: ws://{display}:{port} ({tag})")
        await self._push_status("server_running", panel_id=panel_id,
            host=display, port=port, broadcast=broadcast,
            url=f"ws://{display}:{port}")

    async def _stop_test_server(self, panel_id: str | None = None) -> None:
        for c in list(self._test_clients):
            try:
                await c.close()
            except Exception:
                pass
        self._test_clients.clear()
        self._test_server_host = ""
        self._test_server_port = 0
        if self._test_server:
            self._test_server.close()
            await self._test_server.wait_closed()
            self._test_server = None
            log_info("[Server] 已停止")
            await self._push_log("system", "", "[Server] 已停止", panel_id=panel_id)

    async def _test_server_handler(self, ws: ServerConnection) -> None:
        self._test_clients.add(ws)
        peer = str(ws.remote_address)
        log_info(f"客户端已连接: {peer}")
        await self._push_log("system", peer, "已连接", panel_id=self._server_owner)
        await self._push_status("client_joined", panel_id=self._server_owner, peer=peer)

        try:
            async for msg in ws:
                self._msg_recv += 1
                text = format_message(msg)
                log_recv(f"[{peer}] {text}")
                await self._push_log("recv", peer, text, panel_id=self._server_owner)

                if self._test_server_broadcast:
                    await self._broadcast(msg, sender=ws)
                else:
                    reply = (b"Echo: " + msg) if isinstance(msg, bytes) else f"Echo: {msg}"
                    await ws.send(reply)
                    self._msg_sent += 1
                    rtext = format_message(reply)
                    log_send(f"[→{peer}] {rtext}")
                    await self._push_log("send", peer, rtext, panel_id=self._server_owner)
        except websockets.ConnectionClosed as exc:
            log_debug(f"{peer} 关闭: code={exc.code}")
        except Exception as exc:
            log_warn(f"{peer} 异常: {exc}")
        finally:
            self._test_clients.discard(ws)
            log_info(f"客户端已断开: {peer}")
            await self._push_log("system", peer, "已断开", panel_id=self._server_owner)
            await self._push_status("client_left", panel_id=self._server_owner, peer=peer)

    async def _broadcast(self, msg: str | bytes, sender: ServerConnection | None = None) -> None:
        targets = [c for c in self._test_clients if c != sender]
        if not targets:
            return
        results = await asyncio.gather(*(c.send(msg) for c in targets), return_exceptions=True)
        ok = sum(1 for r in results if r is None)
        self._msg_sent += ok
        text = format_message(msg)
        log_send(f"[广播→{ok}] {text}")
        await self._push_log("send", f"广播→{ok}", text, panel_id=self._server_owner)

    # =========================================================================
    # 动态客户端
    # =========================================================================

    async def _start_client(self, url: str, timeout: float, binary: bool,
                             no_verify: bool, ssl_cert: str | None, ssl_key: str | None,
                             no_ping: bool = False, panel_id: str | None = None) -> None:
        await self._stop_client(panel_id=panel_id)
        ssl_ctx: ssl.SSLContext | None = None
        if url.startswith("wss://"):
            ssl_ctx = ssl.create_default_context()
            if ssl_cert:
                ssl_ctx.load_cert_chain(ssl_cert, keyfile=ssl_key)
            if no_verify:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

        # 保活参数：默认 20s 收不到 pong 判死；服务端不响应 ping 时禁用（None 关闭）
        ping_interval = None if no_ping else 20
        ping_timeout = None if no_ping else 20

        candidates = _connect_candidates(url)

        async def loop() -> None:
            try:
                last_err: Exception | None = None
                for cand in candidates:
                    try:
                        async with websockets.connect(cand, ssl=ssl_ctx, open_timeout=timeout,
                                                      ping_interval=ping_interval,
                                                      ping_timeout=ping_timeout) as ws:
                            self._client_ws = ws
                            log_info(f"[Client] 已连接: {cand}")
                            await self._push_status("client_connected", panel_id=panel_id, url=cand)
                            async for msg in ws:
                                self._msg_recv += 1
                                text = format_message(msg, binary)
                                p = str(ws.remote_address) if ws.remote_address else cand
                                log_recv(f"[{p}] {text}")
                                await self._push_log("recv", p, text if isinstance(msg, str) else format_message(msg, True), panel_id=panel_id)
                        return
                    except (asyncio.TimeoutError, ConnectionRefusedError, OSError,
                            websockets.InvalidURI, websockets.ConnectionClosed) as exc:
                        if self._client_ws is not None:
                            # 已建立连接后发生的断开 → 交给外层统一处理
                            raise
                        last_err = exc
                raise last_err or ConnectionError(f"无法连接 {url}")
            except asyncio.TimeoutError:
                log_error(f"[Client] 连接超时: {url}")
                await self._push_status("error", panel_id=panel_id, info=f"连接超时: {url}")
            except (ConnectionRefusedError, OSError) as exc:
                log_error(f"[Client] 连接失败: {exc}")
                await self._push_status("error", panel_id=panel_id, info=f"连接失败: {exc}")
            except websockets.ConnectionClosed as exc:
                # 对端断开（未走 close 握手 / keepalive ping 超时）——正常断开，非错误
                if exc.code == 1011 and exc.reason and "ping timeout" in exc.reason:
                    log_warn(f"[Client] 保活超时断开: {url}（服务端未响应 ping；可在面板勾选「禁用保活」后重连）")
                else:
                    log_warn(f"[Client] 连接断开: {url} (code={exc.code}, {exc.reason or '对端未发送 close 帧'})")
                await self._push_log("system", "", f"连接断开: code={exc.code}", panel_id=panel_id)
            except Exception as exc:
                log_error(f"[Client] 异常: {exc}")
                await self._push_status("error", panel_id=panel_id, info=str(exc))
            finally:
                self._client_ws = None
                await self._push_status("client_disconnected", panel_id=panel_id)

        self._client_task = asyncio.create_task(loop())

    async def _stop_client(self, panel_id: str | None = None) -> None:
        c = self._client_ws
        self._client_ws = None
        if c:
            try:
                await c.close()
            except Exception:
                pass
        t = self._client_task
        if t and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._client_task = None

    # =========================================================================
    # 发送
    # =========================================================================

    async def _send_to_target(self, text: str, binary: bool, panel_id: str | None = None) -> None:
        data: str | bytes = text
        if binary:
            try:
                data = base64.b64decode(text)
            except Exception:
                await self._push_log("error", "", f"无效 Base64: {text[:60]}", panel_id=panel_id)
                return

        if self._client_ws is not None:
            try:
                await self._client_ws.send(data)
                self._msg_sent += 1
                label = format_message(data, binary)
                log_send(f"[Client] {label}")
                await self._push_log("send", "out", text, panel_id=panel_id)
                return
            except websockets.ConnectionClosed:
                self._client_ws = None
                await self._push_log("error", "", "客户端已断开", panel_id=panel_id)

        if self._test_clients:
            await self._broadcast(data)
            await self._push_log("send", f"广播→{len(self._test_clients)}", text, panel_id=panel_id)
            return

        await self._push_log("system", "", "没有可发送的目标（无连接）", panel_id=panel_id)

    # =========================================================================
    # stdin
    # =========================================================================

    async def _stdin_loop(self) -> None:
        while not self._shutdown.is_set():
            text = await async_stdin_lines(self._shutdown)
            if text is None:
                # stdin EOF（非交互模式）— 不退出，通过 Web 面板操作
                await asyncio.sleep(1)
                continue
            if text.lower() in {"exit", "quit"}:
                self._shutdown.set()
                break
            if not self._test_clients and self._client_ws is None:
                log_warn("没有连接，消息未发送。")
                continue
            await self._send_to_target(text, False)


# ---------------------------------------------------------------------------
