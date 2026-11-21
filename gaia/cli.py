"""CLI da Gaia: `gaia`, `gaia doctor`, `gaia config`, `gaia index`, ..."""

from __future__ import annotations

import asyncio

from gaia.agent import AgentError
from gaia.cli_commands import (
    _config_command,
    _doctor,
    _index_command,
    _note_command,
    _search_command,
)
from gaia.cli_integrations import (
    _browser_command,
    _career_command,
    _telegram_command,
    _whatsapp_command,
)
from gaia.cli_parser import build_parser
from gaia.cli_runtime import _ask_command, _run_app, _watch_mode
from gaia.cli_serve import _serve_command
from gaia.client.remote_client import RemoteClient, RemoteError
from gaia.config import Config
from gaia.interface.terminal import TerminalUI


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.watch:
        _watch_mode()
        return
    cfg = Config.load(args.config)
    if args.ui:
        cfg.interface.ui = args.ui
    if args.tui:
        cfg.interface.ui = "textual"
    if args.old:
        cfg.interface.ui = "terminal"
    ui = TerminalUI()

    try:
        # `--remote` vem PRIMEIRO: o cliente fino nao pode construir o motor
        # local. Com `ask` antes deste ramo, `gaia --remote ... ask` rodava
        # tudo no Mac — inclusive abrindo e indexando o banco local.
        if args.command == "ask" and args.remote:
            cliente = RemoteClient(args.remote, cfg.serve.token, ui=ui)
            resposta = cliente.pedir_um_turno(" ".join(args.message))
            if not resposta:
                raise SystemExit(1)
        elif args.remote and args.command is None:
            RemoteClient(args.remote, cfg.serve.token, ui=ui).run()
        elif args.command == "doctor":
            asyncio.run(_doctor(cfg, ui, args.verbose))
        elif args.command == "config":
            _config_command(cfg, ui, args.init, args.force)
        elif args.command == "index":
            _index_command(cfg, ui, args.force)
        elif args.command == "search":
            _search_command(cfg, ui, " ".join(args.query))
        elif args.command == "note":
            _note_command(cfg, ui, " ".join(args.name))
        elif args.command == "ask":
            asyncio.run(_ask_command(cfg, args, args.verbose, args.no_voice))
        elif args.command == "whatsapp":
            asyncio.run(_whatsapp_command(cfg, args, ui))
        elif args.command == "telegram":
            asyncio.run(_telegram_command(cfg, args, ui))
        elif args.command == "browser":
            asyncio.run(_browser_command(cfg, args, ui))
        elif args.command == "career":
            asyncio.run(_career_command(cfg, args, ui))
        elif args.command == "serve":
            codigo = asyncio.run(_serve_command(cfg, args))
            if codigo:
                raise SystemExit(codigo)
        elif args.remote:
            # Subcomando que nao e `ask` com --remote: ainda assim remoto.
            RemoteClient(args.remote, cfg.serve.token, ui=ui).run()
        else:
            asyncio.run(_run_app(cfg, args.verbose))
    except KeyboardInterrupt:
        ui.info("até logo.")
    except RemoteError as exc:
        ui.error(str(exc))
        raise SystemExit(1) from None
    except AgentError as exc:
        ui.error(str(exc))
        raise SystemExit(1) from None
    except Exception as exc:  # pragma: no cover - fallback amigavel
        if args.verbose:
            raise
        ui.error(f"{type(exc).__name__}: {exc}")
        ui.hint("rode com --verbose para detalhes ou `gaia doctor`")
        raise SystemExit(1) from None
