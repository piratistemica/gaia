"""Comando `zaya serve`: o motor num processo, as telas em outros.

Um processo é dono de tudo — banco, Second Brain, proatividade e o poller do
Telegram — e publica HTTP+SSE para os clientes (`zaya --remote`, web). Nunca
dois processos no mesmo banco: é o que evitava briefing duplicado em 18/11.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

from zaya.cli_runtime import _build_app
from zaya.config import Config
from zaya.interface.remote import RemoteUI
from zaya.serve.app import OrchestratorTarget
from zaya.serve.server import ServeConfigError, ServeServer
from zaya.telegram.service import TelegramService

logger = logging.getLogger(__name__)


def _resolver(cfg: Config, args: Any) -> tuple[str, int, str]:
    host = getattr(args, "host", None) or cfg.serve.host
    porta = getattr(args, "port", None)
    if porta is None:
        porta = cfg.serve.port
    token = getattr(args, "token", None) or cfg.serve.token
    return host, int(porta), token


async def _serve_command(
    cfg: Config, args: Any, parar: asyncio.Event | None = None
) -> int:
    host, porta, token = _resolver(cfg, args)
    quer_telegram = bool(cfg.serve.telegram and cfg.telegram.enabled)
    if getattr(args, "no_telegram", False):
        quer_telegram = False

    ui = RemoteUI()
    try:
        orchestrator, store, index = _build_app(
            cfg, args.verbose, voice=True, quiet=True, channel="serve", ui=ui
        )
    except Exception as exc:  # pragma: no cover - ambiente quebrado
        ui.error(f"nao foi possivel montar o motor: {exc}")
        raise

    alvo = OrchestratorTarget(orchestrator, ui)
    try:
        servidor = ServeServer(alvo, ui, host=host, port=porta, token=token)
    except ServeConfigError as exc:
        ui.error(str(exc))
        ui.hint("defina ZAYA_SERVE_TOKEN no .env ou use --host 127.0.0.1")
        await orchestrator.aclose()
        store.close()
        index.close()
        return 2

    parar = parar or asyncio.Event()

    def _pedir_parada(*_: Any) -> None:
        logger.info("serve: encerrando")
        parar.set()

    loop = asyncio.get_running_loop()
    for sinal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sinal, _pedir_parada)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - Windows
            signal.signal(sinal, _pedir_parada)

    servico_telegram: asyncio.Task[Any] | None = None
    try:
        await orchestrator.start_services()
        alvo.attach_loop(loop)
        url = servidor.start()
        ui.info(f"zaya serve em {url}")
        ui.info(
            f"modelo {cfg.agent.model_slug()} · telegram "
            f"{'ligado' if quer_telegram else 'desligado'} · "
            f"token {'sim' if token else 'nao'}"
        )
        ui.hint(f"cliente: zaya --remote {url}")

        if quer_telegram:
            # A política do chat manda na aprovação quando o Telegram roda aqui.
            from zaya.telegram.service import telegram_permission

            ui.permission_callback = telegram_permission(cfg.telegram)
            servico_telegram = asyncio.create_task(_rodar_telegram(cfg, orchestrator, ui))

        await parar.wait()
    finally:
        if servico_telegram is not None:
            servico_telegram.cancel()
            try:
                await servico_telegram
            except (asyncio.CancelledError, Exception):
                pass
        servidor.shutdown()
        await orchestrator.aclose()
        store.close()
        index.close()
        ui.info("serve encerrado")
    return 0


async def _rodar_telegram(cfg: Config, orchestrator: Any, ui: RemoteUI) -> None:
    from zaya.telegram.client import TelegramClient

    client = TelegramClient(cfg.telegram)
    servico = TelegramService(
        cfg,
        orchestrator,
        client,
        orchestrator.transcriber,
        ui,
        logging.getLogger("zaya.telegram"),
    )
    try:
        await servico.run()
    finally:
        await client.aclose()
