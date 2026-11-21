"""Briefings diarios: checa a Agenda e envia no Telegram, sem LLM."""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime

from gaia.agenda.client import AgendaError
from gaia.briefing.compose import (
    collect_habits,
    collect_tasks,
    day_recap,
    due_slot,
    evening_due,
    evening_message,
    habits_from_routines,
    morning_message,
    noon_message,
    partition,
    weekday_label,
)
from gaia.briefing.market import MarketRow, market_due, market_message
from gaia.controller.contract import OrchestratorContract
from gaia.controller.prompt import humor_directive
from gaia.finance.quotes import fetch_quote

_PERSONAL_NOTE_PROMPT = (
    "Agora são {hora} de {weekday}, {date}. Você é a Gaia, agente pessoal da "
    "Talita, e está mandando a mensagem do dia no Telegram.\n\n"
    "O que você sabe agora:\n{context}\n\n"
    "Escreva UMA frase curta (até 30 palavras), em português do Brasil, informal "
    "e calorosa, que soe feita pra ela hoje — não um aviso genérico. Pode comentar "
    "uma tarefa, o peso do dia, uma meta ou algo que você notou nela. Não repita a "
    "lista, não use emoji, listas, título nem aspas, e não dê conselho "
    "motivacional. Responda APENAS com a frase."
)
_SLOT_LABEL = {"morning": "início da manhã", "noon": "meio do dia"}

_INSIGHT_PROMPT = (
    "Você é a Gaia, agente pessoal da Talita. É o fim da tarde de hoje e ela pediu "
    "um fecho do dia. Use só estas informações da Agenda:\n\n{recap}\n\n"
    "Escreva UM insight curto: 2 a 3 frases, em português do Brasil, informal, em "
    "primeira pessoa, como quem conhece ela. Diga o que o dia revela — o que avançou, "
    "o que travou, o padrão. Nada de lista, nada de título, não comece com emoji e não "
    "dê conselho motivacional. Se o dia foi tranquilo, diga isso sem drama."
)


class BriefingMixin(OrchestratorContract):
    def _start_briefing_worker(self) -> None:
        cfg = self.cfg.briefing
        if not cfg.enabled or not self.telegram.enabled:
            return
        if self._briefing_task is not None and not self._briefing_task.done():
            return
        self._briefing_task = asyncio.create_task(self._briefing_loop())

    async def _briefing_loop(self) -> None:
        cfg = self.cfg.briefing
        await asyncio.sleep(2.0)
        while True:
            await asyncio.sleep(max(5.0, cfg.check_interval_seconds))
            if not cfg.enabled:
                continue
            if self._busy or self._recording or self._listening:
                continue
            now = datetime.now().astimezone()
            day = now.date()
            slot = due_slot(now.strftime("%H:%M"), cfg.morning_at, cfg.noon_at, cfg.noon_until)
            if slot is not None:
                try:
                    await self._send_briefing(slot, day)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # pragma: no cover - depende da rede
                    self.logger.warning("falha no briefing %s: %s", slot, exc, exc_info=True)
            if cfg.market_enabled and market_due(
                now.strftime("%H:%M"),
                cfg.market_at,
                cfg.market_until,
                day.weekday(),
                cfg.market_days,
            ):
                try:
                    await self._send_market_briefing(day)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # pragma: no cover - depende da rede
                    self.logger.warning("falha no resumo da bolsa: %s", exc, exc_info=True)
            if cfg.evening_enabled and evening_due(
                now.strftime("%H:%M"), cfg.evening_at, cfg.evening_until
            ):
                try:
                    await self._send_evening_report(day)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # pragma: no cover - depende da rede
                    self.logger.warning("falha no fecho da noite: %s", exc, exc_info=True)

    async def _send_evening_report(self, day: date) -> bool:
        """Fecha o dia no Telegram uma vez por dia: feito, nao foi e insight."""
        cfg = self.cfg.briefing
        key = f"briefing:{day.isoformat()}:evening"
        if self.store.get_meta(key):
            return False
        if time.monotonic() - self._briefing_last_attempt < cfg.retry_minutes * 60:
            return False
        if not self.store.claim_meta(key, "sending"):
            return False
        self._briefing_last_attempt = time.monotonic()

        try:
            text = await self._compose_evening(day)
        except AgendaError as exc:
            self.logger.warning("fecho da noite: Agenda indisponível: %s", exc)
            self.store.delete_meta(key)
            return False
        except Exception:
            self.store.delete_meta(key)
            raise
        if not text:
            self.store.delete_meta(key)
            return False
        try:
            await self.telegram.send_message(text)
        except Exception as exc:
            self.logger.warning("fecho da noite: falha ao enviar no Telegram: %s", exc)
            self.store.delete_meta(key)
            return False

        self.store.set_meta(key, datetime.now().astimezone().isoformat(timespec="seconds"))
        self.logger.info("fecho da noite de %s enviado", day.isoformat())
        return True

    async def _send_briefing(self, slot: str, day: date) -> bool:
        """Envia uma vez por dia/slot; devolve se enviou agora."""
        cfg = self.cfg.briefing
        key = f"briefing:{day.isoformat()}:{slot}"
        if self.store.get_meta(key):
            return False
        if time.monotonic() - self._briefing_last_attempt < cfg.retry_minutes * 60:
            return False
        # Reserva o slot antes de enviar: TUI e servico do Telegram podem
        # estar abertos ao mesmo tempo e nao devem mandar duas mensagens.
        if not self.store.claim_meta(key, "sending"):
            return False
        self._briefing_last_attempt = time.monotonic()

        try:
            text = await self._compose_briefing(slot, day)
        except AgendaError as exc:
            self.logger.warning("briefing: Agenda indisponível: %s", exc)
            self.store.delete_meta(key)
            return False
        if not text:
            self.store.delete_meta(key)
            return False
        try:
            await self.telegram.send_message(text)
        except Exception as exc:
            self.logger.warning("briefing: falha ao enviar no Telegram: %s", exc)
            self.store.delete_meta(key)
            return False

        self.store.set_meta(key, datetime.now().astimezone().isoformat(timespec="seconds"))
        self.logger.info("briefing %s de %s enviado", slot, day.isoformat())
        return True

    async def _send_market_briefing(self, day: date) -> bool:
        """Envia uma vez por dia o resumo da carteira; devolve se enviou agora."""
        cfg = self.cfg.briefing
        items = self.watchlist.all()
        if not items:
            return False
        key = f"briefing:market:{day.isoformat()}"
        if self.store.get_meta(key):
            return False
        if time.monotonic() - self._market_last_attempt < cfg.retry_minutes * 60:
            return False
        # Reserva antes de buscar: TUI e servico do Telegram podem coexistir.
        if not self.store.claim_meta(key, "sending"):
            return False
        self._market_last_attempt = time.monotonic()

        results = await asyncio.gather(
            *(fetch_quote(item.ticker) for item in items), return_exceptions=True
        )
        rows = [
            MarketRow(
                label=item.label,
                ticker=item.ticker,
                quote=None if isinstance(result, BaseException) else result,
            )
            for item, result in zip(items, results, strict=False)
        ]
        text = market_message(day, rows)
        try:
            await self.telegram.send_message(text)
        except Exception as exc:
            self.logger.warning("briefing: falha ao enviar a bolsa: %s", exc)
            self.store.delete_meta(key)
            return False

        self.store.set_meta(key, datetime.now().astimezone().isoformat(timespec="seconds"))
        self.logger.info("resumo da bolsa de %s enviado", day.isoformat())
        return True

    async def _compose_briefing(self, slot: str, day: date) -> str:
        cfg = self.cfg.briefing
        if not self.agenda.enabled:
            self.logger.info("briefing ignorado: Agenda desabilitada")
            return ""

        notes = await self.agenda.notes_on(day.isoformat())
        tasks = collect_tasks(notes)

        name = self._user_name()
        note = ""
        if cfg.personalize and slot in cfg.personalize_slots:
            note = await self._personal_note(slot, day, tasks)

        if slot == "morning":
            # A mensagem da manhã traz só as tarefas normais, sem rotinas.
            return morning_message(
                name, day, tasks, max_items=cfg.max_tasks, note=note
            )

        return noon_message(name, day, tasks, max_items=cfg.max_tasks, note=note)

    async def _personal_note(self, slot: str, day: date, tasks) -> str:
        """Uma frase feita pra ela hoje; falha vira string vazia, nunca quebra."""
        cfg = self.cfg.briefing
        try:
            metas = await self._evening_metas()
        except Exception:  # pragma: no cover - defensivo
            metas = []
        done_items, pending_items = partition(tasks)
        pending = [item.text for item in pending_items][:6]
        total = len(done_items) + len(pending_items)
        done = len(done_items)
        meta_lines = "\n".join(f"- {meta}" for meta in metas[:5]) or "- (nenhuma)"
        task_lines = "\n".join(f"- {text}" for text in pending) or "- (nenhuma)"
        context = (
            f"Briefing: {_SLOT_LABEL.get(slot, slot)}\n"
            f"Tarefas pendentes ({len(pending_items)} de {total}):\n{task_lines}\n"
            f"Já feitas hoje: {done}\n"
            f"Metas ativas:\n{meta_lines}"
        )
        prompt = _PERSONAL_NOTE_PROMPT.format(
            hora=datetime.now().astimezone().strftime("%H:%M"),
            weekday=weekday_label(day),
            date=day.strftime("%d/%m/%Y"),
            context=context,
        )
        system = "Você é a Gaia, assistente pessoal da Talita. Seja calorosa, informal e espontânea, sem exageros."
        humor = humor_directive(self.cfg.interface.humor)
        if humor:
            system = f"{system} {humor}"
        session_id = ""
        try:
            session = await self.client.create_session(title=f"briefing {slot} {day.isoformat()}")
            session_id = session.id
            message = await asyncio.wait_for(
                self.client.prompt_sync(session_id, prompt, system=system),
                timeout=max(5.0, cfg.personalize_timeout_seconds),
            )
            return " ".join(str(message.text or "").split()).strip().strip('"').strip("'")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - depende do provedor
            self.logger.warning("briefing: comentário pessoal falhou: %s", exc)
            return ""
        finally:
            if session_id:
                try:
                    await self.client.delete_session(session_id)
                except Exception as exc:  # pragma: no cover - depende do provedor
                    self.logger.warning("briefing: falha ao limpar sessão: %s", exc)

    async def _compose_evening(self, day: date) -> str:
        """Lista do dia + insight curto gerado pelo modelo."""
        cfg = self.cfg.briefing
        if not self.agenda.enabled:
            self.logger.info("fecho da noite ignorado: Agenda desabilitada")
            return ""

        notes = await self.agenda.notes_on(day.isoformat())
        tasks = collect_tasks(notes)
        habits, found = collect_habits(notes, cfg.morning_routine)
        if not found:
            routines = await self.agenda.routines()
            habits, _ = habits_from_routines(routines, day, cfg.morning_routine)

        text = evening_message(
            self._user_name(), day, tasks, habits, max_items=cfg.max_tasks
        )
        if cfg.evening_insight:
            metas = await self._evening_metas()
            insight = await self._evening_insight(day, tasks, habits, metas)
            if insight:
                text = f"{text}\n\n{insight}"
        return text

    async def _evening_metas(self) -> list[str]:
        """Metas ativas da Agenda; falha vira lista vazia, nunca quebra o envio."""
        try:
            rows = await self.agenda.metas()
        except Exception as exc:
            self.logger.warning("fecho da noite: metas indisponíveis: %s", exc)
            return []
        return [
            str(row.get("text") or "").strip()
            for row in rows
            if str(row.get("text") or "").strip()
        ]

    async def _evening_insight(
        self, day: date, tasks: list, habits: list, metas: list[str]
    ) -> str:
        """Pede um insight curto do dia ao modelo; falha manda só a lista."""
        cfg = self.cfg.briefing
        prompt = _INSIGHT_PROMPT.format(recap=day_recap(tasks, habits, metas))
        session_id = ""
        try:
            session = await self.client.create_session(
                title=f"insight {day.isoformat()}"
            )
            session_id = session.id
            message = await asyncio.wait_for(
                self.client.prompt_sync(session_id, prompt),
                timeout=max(10.0, cfg.evening_timeout_seconds),
            )
            return str(message.text or "").strip()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - depende do provedor
            self.logger.warning("insight da noite falhou: %s", exc)
            return ""
        finally:
            if session_id:
                try:
                    await self.client.delete_session(session_id)
                except Exception as exc:  # pragma: no cover - depende do provedor
                    self.logger.warning("insight: falha ao limpar sessão: %s", exc)
