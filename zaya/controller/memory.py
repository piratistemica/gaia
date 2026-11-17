"""Rotina de memoria diaria: consolida o dia e expõe o comando /consolidar."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

from zaya.controller.contract import OrchestratorContract
from zaya.memory.consolidation import DailyMemory, parse_day
from zaya.memory.dream import Dreamer, dream_target_day, in_dream_window


class MemoryMixin(OrchestratorContract):
    def _daily_memory(self) -> DailyMemory:
        cache = getattr(self, "_daily_memory_cache", None)
        if cache is None:
            cache = DailyMemory(
                self.cfg,
                self.store,
                self.brain,
                self.index,
                self.client,
                self.directory,
                self.logger,
            )
            self._daily_memory_cache = cache
        return cache

    async def consolidate_day(
        self,
        day: date | None = None,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> str | None:
        return await self._daily_memory().consolidate(
            day, force=force, timeout_seconds=timeout_seconds
        )

    def _dreamer(self) -> Dreamer:
        cache = getattr(self, "_dreamer_cache", None)
        if cache is None:
            cache = Dreamer(
                self.cfg,
                self.store,
                self.brain,
                self.index,
                self.client,
                self.directory,
                self.logger,
            )
            self._dreamer_cache = cache
        return cache

    async def dream_day(
        self,
        day: date | None = None,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> str | None:
        return await self._dreamer().dream(
            day, force=force, timeout_seconds=timeout_seconds
        )

    async def _maybe_consolidate(self, now: datetime) -> None:
        """Consolida o dia quando chega o horario configurado."""
        cfg = self.cfg.memory
        if not cfg.enabled:
            return
        if now.strftime("%H:%M") < cfg.at:
            return
        await self.consolidate_day(now.date())

    async def _maybe_dream(self, now: datetime) -> None:
        """Sonha de madrugada, depois da consolidacao e quando ociosa.

        A janela e ``[dream_at, dream_until)`` e pode virar a meia-noite: o
        padrao (02:00 -> 08:00) sonha sobre o dia que acabou. Ficar fora da
        janela nao perde o sonho: ele entra na proxima madrugada, ainda sobre
        o mesmo dia, porque a guarda e por dia sonhado.
        """
        cfg = self.cfg.memory
        if not cfg.enabled or not cfg.dream_enabled:
            return
        if not in_dream_window(now, cfg.dream_at, cfg.dream_until):
            return
        day = dream_target_day(now)
        # O sonho costura o dia com o passado; garante a memoria do dia antes.
        await self.consolidate_day(day)
        await self.dream_day(day)

    async def _consolidate_previous_day(self) -> None:
        """Recupera o dia anterior se a Zaya nao estava aberta na hora certa."""
        if not self.cfg.memory.enabled:
            return
        yesterday = datetime.now().astimezone().date() - timedelta(days=1)
        await self.consolidate_day(yesterday)

    def _start_memory_worker(self) -> None:
        """Liga a rotina de memoria (catch-up + consolidacao diaria).

        Independente do loop proativo e do tipo de interface: a TUI chama
        ``prepare()``, o terminal chama ``start()`` e o Telegram cai em
        ``answer_once(close=False)``.
        """
        if not self.cfg.memory.enabled or self._memory_task is not None:
            return
        self._memory_task = asyncio.create_task(self._memory_loop())

    async def _memory_loop(self) -> None:
        try:
            await self._consolidate_previous_day()
        except Exception as exc:  # pragma: no cover - depende do provedor
            self.logger.warning("consolidação do dia anterior falhou: %s", exc)
        while True:
            await asyncio.sleep(60.0)
            if not self.cfg.memory.enabled:
                continue
            if self._busy or self._recording or self._listening:
                continue
            try:
                now = datetime.now().astimezone()
                await self._maybe_consolidate(now)
                await self._maybe_dream(now)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - depende do provedor
                self.logger.warning("falha na memória diária: %s", exc)

    async def _handle_consolidate(self, argument: str) -> None:
        day, force = parse_day(argument)
        self.ui.info(f"consolidando a memória de {day.isoformat()}…")
        try:
            path = await self.consolidate_day(
                day, force=force, timeout_seconds=self.cfg.memory.llm_timeout_seconds
            )
        except Exception as exc:
            self.ui.error(f"falha ao consolidar: {exc}")
            return
        if path:
            self.ui.success(f"memória do dia salva em {path}")
        else:
            self.ui.info("nada novo para consolidar (ou já consolidado)")

    async def _handle_dream(self, argument: str) -> None:
        day, force = parse_day(argument)
        self.ui.info(f"sonhando sobre {day.isoformat()}…")
        try:
            await self.consolidate_day(day)
            path = await self.dream_day(
                day, force=force, timeout_seconds=self.cfg.memory.llm_timeout_seconds
            )
        except Exception as exc:
            self.ui.error(f"falha ao sonhar: {exc}")
            return
        if path:
            self.ui.success(f"sonho salvo em {path}")
        else:
            self.ui.info("nada para sonhar (ou já sonhado)")
