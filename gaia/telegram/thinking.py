"""Raciocinio do modelo visivel no Telegram, numa mensagem editada aos poucos.

O sink recebe o texto acumulado do raciocinio (o mesmo que a TUI mostra na
linha de status) e mantem UMA mensagem no chat: a primeira escrita envia, as
seguintes editam. Editar de 2,5s em 2,5s respeita o limite do Telegram e evita
encher o chat de mensagens. Falha de rede nunca derruba o turno: o pior caso e
o "pensando" parar de atualizar.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

HEADER = "🧠 pensando…"


class _TelegramLike(Protocol):
    async def send_message(
        self, text: str, chat_id: str | int | None = None
    ) -> dict[str, Any]: ...

    async def edit_message_text(
        self, message_id: int, text: str, chat_id: str | int | None = None
    ) -> dict[str, Any]: ...


class ThinkingSink:
    """StreamSink que publica o raciocinio no Telegram (ativado por /pensando)."""

    # O orquestrador consulta este atributo para entregar o raciocinio mesmo
    # quando a TUI esta com o raciocinio desligado.
    wants_reasoning = True

    def __init__(
        self,
        client: _TelegramLike,
        logger: logging.Logger,
        chat_id: str | int | None = None,
        *,
        min_interval: float = 2.5,
        max_chars: int = 3500,
    ) -> None:
        self._client = client
        self._logger = logger
        self._chat_id = chat_id
        self._min_interval = max(0.0, min_interval)
        self._max_chars = max(200, max_chars)
        self._body = ""
        self._status = ""
        self._message_id: int | None = None
        self._pushed_text = ""
        self._last_push = 0.0
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ StreamSink
    def token(self, text: str) -> None:
        """A resposta final sai pelo caminho normal; aqui nao faz nada."""

    def diff(self, filename: str, patch: str) -> None:
        """Diff de ferramenta nao cabe no pensamento."""

    def reasoning(self, text: str) -> None:
        self._body = text or ""
        self._schedule()

    def tool(self, name: str, detail: str = "") -> None:
        line = f"▸ {name} {detail}".strip()
        self._status = line
        self._schedule()

    # ------------------------------------------------------------- publico
    async def aclose(self) -> None:
        """Espera a atualizacao em voo e publica o raciocinio completo."""
        task = self._task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        self._task = None
        await self._push()

    # ------------------------------------------------------------ interno
    def _compose(self) -> str:
        body = self._body.strip()
        if len(body) > self._max_chars:
            body = "…" + body[-self._max_chars :]
        parts = [HEADER]
        if body:
            parts.append(body)
        if self._status:
            parts.append(f"_{self._status}_")
        return "\n\n".join(parts)

    def _schedule(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if (
            self._message_id is not None
            and time.monotonic() - self._last_push < self._min_interval
        ):
            return
        self._task = asyncio.create_task(self._push())

    async def _push(self) -> None:
        text = self._compose()
        if not text.strip() or text == self._pushed_text:
            return
        async with self._lock:
            try:
                if self._message_id is None:
                    sent = await self._client.send_message(text, chat_id=self._chat_id)
                    if isinstance(sent, dict):
                        self._message_id = sent.get("message_id")
                else:
                    await self._client.edit_message_text(
                        self._message_id, text, chat_id=self._chat_id
                    )
                self._pushed_text = text
            except Exception as exc:  # pragma: no cover - depende da rede
                self._logger.debug("thinking: falha ao publicar o raciocinio: %s", exc)
            finally:
                self._last_push = time.monotonic()
