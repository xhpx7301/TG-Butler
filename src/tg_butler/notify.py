from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)


def chunks(text: str, size: int = 3500) -> list[str]:
    # Telegram counts UTF-16 code units. 3500 UTF-8 bytes is safely below its limit.
    result, current, length = [], [], 0
    for char in text:
        width = len(char.encode("utf-8"))
        if length + width > size:
            result.append("".join(current))
            current, length = [], 0
        current.append(char)
        length += width
    if current:
        result.append("".join(current))
    return result


class Notifier:
    def __init__(self, token: str, chat_id: int, queue_size: int = 500):
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._url = f"{self._base_url}/sendMessage"
        self.chat_id = chat_id
        self.http = httpx.AsyncClient(timeout=30)
        self.queue: asyncio.Queue[tuple[str, int | None, int | None, str | None]] = asyncio.Queue(
            maxsize=queue_size
        )
        self.lock = asyncio.Lock()
        self.dropped = 0

    async def send(self, text: str, parse_mode: str | None = None) -> None:
        async with self.lock:
            for part in chunks(text):
                await self._send_part(part, parse_mode)

    async def _send_part(self, text: str, parse_mode: str | None = None) -> None:
        for attempt in range(3):
            try:
                response = await self.http.post(
                    self._url,
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "link_preview_options": {"is_disabled": True},
                        **({"parse_mode": parse_mode} if parse_mode else {}),
                    },
                )
            except httpx.TransportError:
                if attempt == 2:
                    raise RuntimeError("Telegram notification network failure") from None
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code == 429:
                try:
                    delay = int(response.json().get("parameters", {}).get("retry_after", 5))
                except (ValueError, TypeError):
                    delay = 5
                if attempt < 2 and 0 <= delay <= 60:
                    await asyncio.sleep(delay)
                    continue
            if response.status_code >= 500 and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code != 200 or response.json().get("ok") is not True:
                # Never include the URL: it contains the bot token.
                raise RuntimeError(f"Telegram notification rejected (HTTP {response.status_code})")
            return
        raise RuntimeError("Telegram notification attempts exhausted")

    def enqueue(self, text: str, parse_mode: str | None = None) -> None:
        self._enqueue((text, None, None, parse_mode))

    def enqueue_copy(
        self, text: str, from_chat_id: int, message_id: int, parse_mode: str | None = None
    ) -> None:
        self._enqueue((text, from_chat_id, message_id, parse_mode))

    def _enqueue(self, item: tuple[str, int | None, int | None, str | None]) -> None:
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped += 1
            log.warning(
                "Live alert queue full; dropped count=%d; message remains in database", self.dropped
            )

    async def worker(self) -> None:
        while True:
            text, from_chat_id, message_id, parse_mode = await self.queue.get()
            try:
                if self.dropped:
                    count = self.dropped
                    await self.send(
                        f"TG-Butler：通知队列拥堵，{count} 条实时提醒未发送；消息仍在本地数据库。"
                    )
                    self.dropped -= count
                await self.send(text, parse_mode)
                if from_chat_id is not None and message_id is not None:
                    await self._copy_message(from_chat_id, message_id)
            except Exception as exc:
                log.error("Notification delivery failed (%s)", type(exc).__name__)
            finally:
                self.queue.task_done()

    async def _copy_message(self, from_chat_id: int, message_id: int) -> None:
        try:
            response = await self.http.post(
                f"{self._base_url}/copyMessage",
                json={
                    "chat_id": self.chat_id,
                    "from_chat_id": from_chat_id,
                    "message_id": message_id,
                },
            )
        except httpx.TransportError:
            raise RuntimeError("Telegram original message copy failed (network error)") from None
        if response.status_code != 200 or response.json().get("ok") is not True:
            raise RuntimeError(
                f"Telegram original message copy rejected (HTTP {response.status_code}); "
                "ensure the custom bot can access the source group"
            )

    async def close(self) -> None:
        await self.http.aclose()
