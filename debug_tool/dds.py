"""DDS 引擎 —— 基于 Cyclone DDS 的发布/订阅实现。

注意：本模块 *不* 启用 `from __future__ import annotations`。Cyclone DDS 的
IdlStruct 在类定义阶段需要真实的字段类型对象（而非 PEP 563 的字符串注解）
来构建 IDL 类型描述符，否则 Topic/DataWriter 创建或 write 时会抛异常。
"""

import asyncio
import dataclasses
from typing import Any

try:
    from cyclonedds.domain import DomainParticipant
    from cyclonedds.topic import Topic
    from cyclonedds.pub import DataWriter
    from cyclonedds.sub import DataReader
    from cyclonedds.idl import IdlStruct
    HAS_CYCLONEDDS = True
except (ImportError, ModuleNotFoundError):
    HAS_CYCLONEDDS = False
    DomainParticipant = None  # type: ignore
    Topic = None              # type: ignore
    DataWriter = None         # type: ignore
    DataReader = None         # type: ignore
    IdlStruct = None          # type: ignore

from debug_tool.logger import log_recv

# ---------------------------------------------------------------------------
# DDS 消息类型
# ---------------------------------------------------------------------------

if HAS_CYCLONEDDS:

    @dataclasses.dataclass
    class DdsMessage(IdlStruct):
        text: str
else:

    @dataclasses.dataclass
    class DdsMessage:
        text: str


# ---------------------------------------------------------------------------
# DDS 引擎
# ---------------------------------------------------------------------------

class DdsEngine:
    """Cyclone DDS 发布/订阅引擎。"""

    def __init__(self, domain: int, push_log: Any, push_status: Any) -> None:
        self.domain = domain
        self._log = push_log
        self._status = push_status
        self._topic_name = "DdsDebug"
        self._pub_active = False
        self._sub_active = False
        self._sub_task: asyncio.Task | None = None
        self._shutdown = asyncio.Event()
        self._msg_sent = 0
        self._msg_recv = 0
        self._dp: Any = None
        self._topic: Any = None
        self._writer: Any = None
        self._reader: Any = None
        self._pub_owner: str | None = None
        self._sub_owner: str | None = None

    @staticmethod
    def available() -> bool:
        return HAS_CYCLONEDDS

    async def _ensure_entities(self, panel_id: str | None = None) -> None:
        if self._dp is not None:
            return
        loop = asyncio.get_running_loop()
        try:
            self._dp = await loop.run_in_executor(
                None, lambda: DomainParticipant(domain_id=self.domain))
            self._topic = await loop.run_in_executor(
                None, lambda: Topic(self._dp, self._topic_name, DdsMessage))
            self._writer = await loop.run_in_executor(
                None, lambda: DataWriter(self._dp, self._topic))
            self._reader = await loop.run_in_executor(
                None, lambda: DataReader(self._dp, self._topic))
        except Exception as exc:
            # 实体创建失败要清理半成品，并把真实异常推到面板，避免"点击没反应"
            self._dp = self._topic = self._writer = self._reader = None
            import traceback
            tb = traceback.format_exc()
            await self._log("error", "", f"DDS 实体创建失败: {exc}", panel_id)
            await self._log("error", "", tb, panel_id)
            raise
        await self._log("system", "",
            f"DDS 实体已创建 (domain={self.domain}, topic={self._topic_name})", panel_id)

    # ---- 发布 ----------
    async def start_pub(self, panel_id: str | None = None) -> None:
        if not HAS_CYCLONEDDS:
            await self._log("error", "", "Cyclone DDS 未安装，请: pip install cyclonedds", panel_id)
            return
        self._pub_owner = panel_id
        await self._ensure_entities(panel_id)
        self._pub_active = True
        await self._log("system", "",
            f"DDS Publisher 就绪 (domain={self.domain}, topic={self._topic_name})", panel_id)

    async def stop_pub(self) -> None:
        self._pub_active = False
        await self._log("system", "", "DDS Publisher 已停止", self._pub_owner)
        self._pub_owner = None

    # ---- 订阅 ----------
    async def start_sub(self, panel_id: str | None = None) -> None:
        if not HAS_CYCLONEDDS:
            await self._log("error", "", "Cyclone DDS 未安装，请: pip install cyclonedds", panel_id)
            return
        self._sub_owner = panel_id
        await self._ensure_entities(panel_id)
        self._shutdown.clear()
        self._sub_active = True
        self._sub_task = asyncio.create_task(self._sub_loop())
        await self._log("system", "",
            f"DDS Subscriber 启动 (domain={self.domain}, topic={self._topic_name})", panel_id)

    async def stop_sub(self) -> None:
        self._sub_active = False
        self._shutdown.set()
        if self._sub_task and not self._sub_task.done():
            self._sub_task.cancel()
            try:
                await self._sub_task
            except asyncio.CancelledError:
                pass
        self._sub_task = None
        await self._log("system", "", "DDS Subscriber 已停止", self._sub_owner)
        self._sub_owner = None

    async def _sub_loop(self) -> None:
        if self._reader is None:
            await self._log("error", "", "DDS DataReader 未初始化", self._sub_owner)
            return
        loop = asyncio.get_running_loop()
        while not self._shutdown.is_set():
            samples = await loop.run_in_executor(None, self._reader.take)
            for sample in samples:
                self._msg_recv += 1
                text = sample.text
                log_recv(f"[DDS/{self._topic_name}] {text}")
                await self._log("recv", f"DDS(topic={self._topic_name})", text, self._sub_owner)
            await asyncio.sleep(0.1)
        await self._log("system", "", "DDS Subscriber 监听已退出", self._sub_owner)

    # ---- 发送 ----------
    async def send(self, text: str, panel_id: str | None = None) -> None:
        if not HAS_CYCLONEDDS:
            await self._log("error", "", "Cyclone DDS 未安装", panel_id)
            return
        if self._writer is None:
            await self._ensure_entities(panel_id)
        if self._writer is None:
            await self._log("error", "", "DDS DataWriter 未初始化", panel_id)
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._writer.write, DdsMessage(text=text))
        self._msg_sent += 1
        await self._log("send", f"DDS(topic={self._topic_name})", text, panel_id)

    async def stop(self) -> None:
        await self.stop_sub()
        await self.stop_pub()
        self._dp = None
        self._topic = None
        self._writer = None
        self._reader = None
