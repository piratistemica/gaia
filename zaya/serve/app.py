"""O motor por trás do transporte: liga `Orchestrator` ao `Target` do serve.

Aqui (e só aqui) o transporte conhece o agente. O `Orchestrator` roda num loop
asyncio numa thread; o servidor HTTP roda em outra. A ponte é
``asyncio.run_coroutine_threadsafe`` — assíncrono de um lado, thread do outro.

Quem responde aprovação é o `RemoteUI`: o pedido chega como evento `permission`
e fica esperando (numa thread) até ``POST /permission/{id}`` destravar.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import logging
import secrets
from typing import Any

from zaya.interface.remote import RemoteUI

logger = logging.getLogger(__name__)


class MotorOcupado(RuntimeError):
    """Já existe um turno em andamento; o transporte traduz em HTTP 409."""


class OrchestratorTarget:
    """`Target` do `zaya serve`, sobre um `Orchestrator` real."""

    def __init__(self, orchestrator: Any, ui: RemoteUI) -> None:
        self.orch = orchestrator
        self.ui = ui
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tarefa: concurrent.futures.Future[Any] | None = None
        self._run = ""

    # ------------------------------------------------------------------ ciclo
    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def ocupado(self) -> bool:
        return self._tarefa is not None and not self._tarefa.done()

    # ------------------------------------------------------------------ rotas
    def health(self) -> dict[str, Any]:
        cfg = self.orch.cfg
        return {
            "version": getattr(__import__("zaya"), "__version__", ""),
            "model": cfg.agent.model_slug(),
            "session": getattr(self.orch, "session_id", ""),
            "mode": getattr(self.orch, "mode_name", lambda: "")(),
            "busy": self.ocupado,
        }

    def sessions(self) -> list[dict[str, Any]]:
        return list(self.orch.sessions())

    def turn(self, text: str) -> str:
        if self.ocupado:
            raise MotorOcupado("turno em andamento")
        if self._loop is None:
            raise RuntimeError("serve sem loop asyncio ligado")
        self._run = f"r_{secrets.token_hex(4)}"
        self.ui.run = self._run
        self.ui.emit("run_started", text=text)
        self._tarefa = asyncio.run_coroutine_threadsafe(
            self._executar(text), self._loop
        )
        return self._run

    def decide(self, request_id: str, decision: str) -> None:
        if not self.ui.resolve_permission(request_id, decision):
            logger.debug("permissao %s sem ninguem esperando", request_id)

    def abort(self) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._abortar(), self._loop)

    # ------------------------------------------------------------------ interno
    async def _executar(self, text: str) -> None:
        try:
            resposta = await self.orch.answer_once(text, speak=False, close=False)
        except Exception as exc:  # nao derruba o servidor por um turno ruim
            logger.warning("turno remoto falhou: %s", exc, exc_info=True)
            self.ui.emit("run_finished", ok=False, error=str(exc))
            return
        self.ui.emit("run_finished", ok=True, text=resposta or "")

    async def _abortar(self) -> None:
        abort = getattr(self.orch.client, "abort", None)
        if abort is None:
            return
        resultado = abort(self.orch.session_id)
        if inspect.isawaitable(resultado):
            await resultado
        self.ui.emit("aborted")
