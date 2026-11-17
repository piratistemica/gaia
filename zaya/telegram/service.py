"""Serviço que escuta o Telegram e encaminha mensagens para a Zaya."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from zaya.config import Config, TelegramConfig
from zaya.controller.orchestrator import Orchestrator
from zaya.interface.terminal import TerminalUI
from zaya.stt.faster_whisper import STTError, WhisperTranscriber
from zaya.telegram.client import TelegramClient, TelegramError

# Teto do backoff do long polling. Sem isso, uma queda do Telegram vira rajada
# de requisicoes a cada 2s durante todo o incidente.
_MAX_BACKOFF = 30.0


# Ferramentas que dão shell ou escrevem arquivo: nunca pelo chat remoto sem
# opt-in explícito (telegram.shell_access).
_SHELL_TOOLS = frozenset({"bash", "write", "edit"})


def telegram_permission(cfg: TelegramConfig) -> Callable[[str, list[str]], str]:
    """Política de permissão para o canal do Telegram.

    O serviço sobe com ``TerminalUI(quiet=True)``; sem terminal interativo o
    prompt de permissão responde "reject" e as ferramentas que pedem
    confirmação nunca funcionariam. Esta política substitui o prompt.

    Duas travas independentes:

    1. ``auto_approve`` só vale com ``allowed_chat_id`` fixado — sem isso o bot
       adota o primeiro chat que falar com ele, e auto-aprovar daria shell a
       qualquer desconhecido.
    2. ``bash``/``write``/``edit`` continuam negados mesmo com auto_approve,
       a menos que ``shell_access`` esteja ligado. A Agenda é acessada pelas
       ferramentas ``agenda_*``, então o chat não precisa de shell.
    """
    enabled = bool(cfg.auto_approve and str(cfg.allowed_chat_id or "").strip())

    def decide(tool: str, resources: list[str]) -> str:
        if tool in _SHELL_TOOLS and not cfg.shell_access:
            return "reject"
        return "always" if enabled else "reject"

    return decide


class TelegramService:
    def __init__(
        self,
        cfg: Config,
        orchestrator: Orchestrator,
        client: TelegramClient,
        transcriber: WhisperTranscriber,
        ui: TerminalUI,
        logger: logging.Logger,
    ):
        self.cfg = cfg
        self.orchestrator = orchestrator
        self.client = client
        self.transcriber = transcriber
        self.ui = ui
        self.logger = logger
        self._chat_id: str | None = None

    async def run(self) -> None:
        username = "?"
        try:
            me = await self.client.get_me()
            username = me.get("username", "?") if isinstance(me, dict) else "?"
        except TelegramError as exc:
            # Um 502/timeout aqui nao pode matar o processo: o KeepAlive do
            # launchd reiniciaria em loop e o bot ficaria mudo ate a API voltar.
            # O loop de getUpdates abaixo ja trata falha com backoff.
            self.logger.warning("getMe falhou no boot: %s", exc)
        self.ui.info(f"Telegram conectado: @{username}")
        self.ui.hint("enviando mensagens; Ctrl+C encerra")
        # Briefings diarios rodam mesmo sem mensagem recebida.
        self.orchestrator.start_briefing()
        # A rotina semanal de vagas tambem: sem isso ela so acordaria depois
        # da primeira mensagem, e a segunda-feira passaria em branco.
        self.orchestrator.start_career()
        # Sem isso o servico responde mas nunca dispara lembrete nem aviso de
        # agenda: o laco proativo so existia no caminho da TUI.
        self.orchestrator.start_proactive()
        # Consolidacao das 23:00 e sonho das 02:00 tambem precisam subir sem
        # mensagem: headless, ninguem fala com ela de madrugada.
        self.orchestrator.start_memory()
        offset: int | None = None
        backoff = 2.0
        while True:
            try:
                updates = await self.client.get_updates(offset=offset)
                backoff = 2.0
            except TelegramError as exc:
                self.logger.warning("getUpdates falhou: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
                continue
            for update in updates:
                offset = int(update.get("update_id", 0)) + 1
                try:
                    await self._handle_update(update)
                except Exception as exc:  # pragma: no cover - depende da rede/agente
                    self.logger.exception("falha ao processar update")
                    await self._safe_reply(update, f"Deu erro aqui: {exc}")
            await asyncio.sleep(0.05)

    def _allowed(self, chat_id: Any) -> bool:
        allowed = str(self.cfg.telegram.allowed_chat_id or "").strip()
        if allowed:
            return str(chat_id) == allowed
        if self._chat_id is None:
            self._chat_id = str(chat_id)
            self.ui.info(f"chat autorizado automaticamente: {self._chat_id}")
        return str(chat_id) == self._chat_id

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        if chat_id is None or not self._allowed(chat_id):
            return
        # Espelhar o canal: entrou audio, sai audio. `voice_replies` continua
        # servindo para responder em voz tambem quando a entrada e texto.
        was_voice = bool(message.get("voice") or message.get("audio"))
        text = (message.get("text") or "").strip()
        if text in {"/start", "/iniciar"}:
            await self.client.send_message(
                "Oi! Sou a Zaya. Manda texto ou áudio que eu executo e te aviso "
                "aqui quando terminar. Se quiser começar do zero, manda /novo.",
                chat_id=chat_id,
            )
            return
        if text.lower() in {"/novo", "/nova", "/novo chat", "/nova sessao", "/nova sessão"}:
            await self._start_new_session(chat_id)
            return
        if text == "/modelo" or text.startswith("/modelo ") or text in {"/modelos", "/model"}:
            await self._handle_model_command(text, chat_id)
            return
        if not text:
            text = await self._transcribe_message(message)
        if not text:
            return

        await self.client.send_chat_action(chat_id, "typing")
        await self.client.send_message("⏳ trabalhando nisso…", chat_id=chat_id)
        try:
            answer = await self.orchestrator.answer_once(text, speak=False, close=False)
        except Exception as exc:
            self.logger.exception("falha no agente")
            answer = f"Deu erro aqui: {exc}"

        if answer:
            await self.client.send_message(answer, chat_id=chat_id)
            if was_voice or self.cfg.telegram.voice_replies:
                await self._send_voice(answer, chat_id)
        elif self.orchestrator.last_error:
            await self.client.send_message(
                f"Não consegui responder: {self.orchestrator.last_error}", chat_id=chat_id
            )
        else:
            await self.client.send_message("Concluído.", chat_id=chat_id)

    async def _start_new_session(self, chat_id: Any) -> None:
        """`/novo` no Telegram = mesma coisa que `/novo` no terminal."""
        create = getattr(self.orchestrator, "new_session", None)
        if create is None:
            await self.client.send_message(
                "Esse canal não sabe criar sessão nova.", chat_id=chat_id
            )
            return
        try:
            await create()
        except Exception as exc:
            self.logger.exception("falha ao criar sessão")
            await self.client.send_message(f"Não consegui criar: {exc}", chat_id=chat_id)
            return
        await self.client.send_message("Pronto, sessão nova. Contexto zerado.", chat_id=chat_id)

    async def _handle_model_command(self, text: str, chat_id: Any) -> None:
        """`/modelo` no Telegram: lista numerada ou troca pelo número/slug."""
        switch = getattr(self.orchestrator, "model_messages", None)
        if not callable(switch):
            await self.client.send_message(
                "Esse canal não sabe trocar de modelo.", chat_id=chat_id
            )
            return
        argument = text.partition(" ")[2].strip()
        if text.strip() in {"/modelos", "/model"} and not argument:
            argument = ""
        try:
            messages = await switch(argument)
        except Exception as exc:
            self.logger.exception("falha ao trocar modelo")
            await self.client.send_message(
                f"Não consegui trocar o modelo: {exc}", chat_id=chat_id
            )
            return
        lines = [body for kind, body in messages if kind != "info" or body]
        await self.client.send_message("\n".join(lines) or "ok", chat_id=chat_id)

    async def _transcribe_message(self, message: dict[str, Any]) -> str:
        media = message.get("voice") or message.get("audio")
        if not media:
            return ""
        file_id = media.get("file_id")
        if not file_id:
            return ""
        self.ui.info("baixando áudio…")
        file_path = await self.client.get_file(str(file_id))
        data = await self.client.download_file(file_path)
        mime = str(media.get("mime_type") or "")
        if "ogg" in mime:
            suffix = ".ogg"
        elif "m4a" in mime or "mp4" in mime:
            suffix = ".m4a"
        else:
            suffix = ".mp3"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(data)
            tmp = Path(fh.name)
        try:
            self.ui.info(f"transcrevendo áudio ({len(data)} bytes)…")
            return await asyncio.to_thread(self.transcriber.transcribe_file, tmp)
        except STTError as exc:
            self.logger.warning("transcrição falhou: %s", exc)
            return ""
        finally:
            tmp.unlink(missing_ok=True)

    async def _send_voice(self, text: str, chat_id: Any) -> None:
        prepared = await self.orchestrator.tts.prepare(text)
        if prepared is None:
            return
        backend, path = prepared
        try:
            await self.client.send_audio(path, chat_id=chat_id, as_voice=True)
        finally:
            backend.cleanup(path)

    async def _safe_reply(self, update: dict[str, Any], text: str) -> None:
        chat_id = ((update.get("message") or {}).get("chat") or {}).get("id")
        if chat_id is None:
            return
        try:
            await self.client.send_message(text, chat_id=chat_id)
        except Exception as exc:
            self.logger.debug("falha ao responder no Telegram: %s", exc)
