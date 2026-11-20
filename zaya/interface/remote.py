"""Interface remota: em vez de desenhar, serializa.

O controller conversa com a interface por uma superficie *duck-typed* (as ~30
chamadas ``ui.*``). ``TerminalUI`` e ``TextualUI`` desenham; ``RemoteUI`` empurra
um dicionario num ``queue.Queue`` que o servidor ``zaya serve`` transforma em SSE.
Nenhum metodo aqui toca ``stdout``, abre arquivo ou bloqueia em ``input()``.

Contrato do fio: ``docs/plans/2026-11-19-serve-cliente-fino.md`` (§4.1). Os nomes
seguem AG-UI onde existir equivalente, para que expor ``/agui`` depois nao exija
refatorar o cliente.
"""

from __future__ import annotations

import dataclasses
from collections import deque
from datetime import date, datetime
from pathlib import Path
from queue import Queue
from typing import Any

EVENT_VERSION = 1
# Quantos eventos ficam em memoria para um cliente que reconecta com ?since=.
HISTORY = 1000

# Todo metodo que o resto do codigo chama em `ui`. A varredura em
# `tests/test_remote_ui.py` falha se alguem usar `ui.<novo>(` sem entrar aqui —
# e portanto sem aparecer no cliente remoto.
RENDER_METHODS = (
    "banner",
    "begin_stream",
    "check",
    "clear_status",
    "close",
    "context_hits",
    "dashboard",
    "diff",
    "end_stream",
    "erase_input_line",
    "error",
    "finish_stream",
    "help",
    "hint",
    "info",
    "note_preview",
    "partial",
    "permission",
    "print_markdown",
    "print_note",
    "print_transcript",
    "print_user",
    "proactive",
    "prompt",
    "reasoning",
    "reminders",
    "search_results",
    "set_status",
    "status",
    "success",
    "token",
    "tool",
    "update_stream",
    "warning",
    "watchlist",
    "write_diff",
)

# Metodos que o transporte usa (o servidor assina o stream), nao a UI.
STREAM_METHODS = ("subscribe", "unsubscribe")

_LEVELS = {
    "hint": "hint",
    "info": "info",
    "success": "success",
    "warning": "warning",
    "error": "error",
}


def _plain(value: Any) -> Any:
    """Converte o que vier em algo que `json.dumps` aceita."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_plain(item) for item in value]
    # Modelos pydantic e objetos com __dict__ simples.
    dumped = getattr(value, "model_dump", None)
    if callable(dumped):
        return _plain(dumped())
    if hasattr(value, "__dict__"):
        return {k: _plain(v) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)


class _ConsoleShim:
    """O que o controller usa de `ui.console`: largura e `print`."""

    width = 80

    def __init__(self, ui: RemoteUI) -> None:
        self._ui = ui

    def print(self, *objects: Any, **_: Any) -> None:
        text = " ".join(str(item) for item in objects)
        if text.strip():
            self._ui.emit("say", text=text)


class RemoteUI:
    """Superficie de interface que produz eventos em vez de pixels."""

    def __init__(
        self,
        events: Queue[dict[str, Any]] | None = None,
        *,
        run: str = "",
    ) -> None:
        self.events: Queue[dict[str, Any]] = events if events is not None else Queue()
        self.run = run
        # Buffer para reconexao (?since=<seq>) e lista de assinantes: cada um
        # recebe tudo, para dois clientes nao dividirem o stream.
        self.history: deque[dict[str, Any]] = deque(maxlen=HISTORY)
        self._subscribers: list[Queue[dict[str, Any]]] = []
        self.console = _ConsoleShim(self)
        # Compatibilidade com o hook que `TerminalUI` expoe: o caminho remoto
        # nao o usa (o runtime emite `PermissionAsked`), mas existir evita
        # quebra em quem testa `hasattr`.
        self.permission_callback: Any = None
        self._seq = 0
        self._streaming = False

    # ---------------------------------------------------------------- emissao
    def emit(self, kind: str, **payload: Any) -> dict[str, Any]:
        """Numera, carimba e enfileira um evento. Devolve o dicionario."""
        self._seq += 1
        event: dict[str, Any] = {
            "v": EVENT_VERSION,
            "seq": self._seq,
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "type": kind,
        }
        if self.run:
            event["run"] = self.run
        for key, value in payload.items():
            event[key] = _plain(value)
        self.history.append(event)
        self.events.put(event)
        for subscriber in list(self._subscribers):
            subscriber.put(event)
        return event

    # --------------------------------------------------------- assinantes/SSE
    def subscribe(self, since: int | None = None) -> Queue[dict[str, Any]]:
        """Fila só deste cliente, já semeada com o que ele perdeu."""
        fila: Queue[dict[str, Any]] = Queue()
        if since is not None:
            for event in self.history:
                if event.get("seq", 0) > since:
                    fila.put(event)
        self._subscribers.append(fila)
        return fila

    def unsubscribe(self, fila: Queue[dict[str, Any]]) -> None:
        if fila in self._subscribers:
            self._subscribers.remove(fila)

    def _notice(self, level: str, text: str) -> None:
        self.emit("notice", level=level, text=text)

    # ------------------------------------------------------- avisos e textos
    def hint(self, text: str) -> None:
        self._notice("hint", text)

    def info(self, text: str) -> None:
        self._notice("info", text)

    def success(self, text: str) -> None:
        self._notice("success", text)

    def warning(self, text: str) -> None:
        self._notice("warning", text)

    def error(self, text: str) -> None:
        self._notice("error", text)

    def check(self, ok: bool, label: str, detail: str = "") -> None:
        self.emit("check", ok=bool(ok), label=label, detail=detail)

    def print_markdown(self, text: str, title: str | None = None) -> None:
        self.emit("say", text=text, title=title)

    def print_user(self, text: str, voice: bool = False) -> None:
        self.emit("user", text=text, voice=bool(voice))

    def print_transcript(self, text: str) -> None:
        self.emit("transcript", text=text)

    def partial(self, text: str) -> None:
        self.emit("partial", text=text)

    def proactive(self, text: str) -> None:
        self.emit("proactive", text=text)

    # ------------------------------------------------------------------ stream
    def begin_stream(self) -> None:
        self._streaming = True
        self.emit("stream_begin")

    def update_stream(self, text: str) -> None:
        self.emit("stream_delta", text=text)

    def end_stream(self) -> None:
        self._streaming = False
        self.emit("stream_end")

    def finish_stream(self, answer: str) -> None:
        self._streaming = False
        self.emit("stream_end", text=answer)

    def write_diff(self, filename: str, patch: str) -> None:
        if patch.strip():
            self.emit("tool", name="diff", detail=filename, status="ok", diff=patch)

    def diff(self, filename: str, patch: str) -> None:
        self.write_diff(filename, patch)

    # ------------------------------------------------------------- ferramentas
    def tool(self, name: str, detail: str = "") -> None:
        self.emit("tool", name=name, detail=detail, status="running")

    def reasoning(self, text: str) -> None:
        self.emit("reasoning", text=text)

    def permission(self, tool: str, patterns: list[str]) -> str:
        """Nao bloqueia e nao pergunta.

        O runtime ja emite `PermissionAsked` e espera num Future; a resposta vem
        por `POST /permission/{id}`. Este metodo existe so para satisfazer a
        superficie e nunca deve ser o caminho de uma aprovacao.
        """
        self.emit("permission_ignored", tool=tool, resources=patterns)
        return "reject"

    # ------------------------------------------------------------------ status
    def set_status(self, label: str, detail: str = "") -> None:
        self.emit("status", text=label, detail=detail)

    def clear_status(self) -> None:
        self.emit("status", text=None)

    def status(
        self,
        project: Path,
        session_id: str,
        model: str,
        sb_notes: int,
        voice: str,
    ) -> None:
        self.emit(
            "session",
            project=str(project),
            session=session_id,
            model=model,
            notes=sb_notes,
            voice=voice,
        )

    def banner(
        self,
        project: Path,
        session_id: str,
        model: str,
        sb_notes: int,
        voice: str,
        tts_enabled: bool = True,
    ) -> None:
        self.emit(
            "banner",
            project=str(project),
            session=session_id,
            model=model,
            notes=sb_notes,
            voice=voice,
            tts=bool(tts_enabled),
        )

    def token(self, text: str) -> None:
        self.emit("tokens", text=text)

    def dashboard(
        self,
        focus: Any,
        goals: list[Any],
        moon: Any = None,
        moon_note: str = "",
    ) -> None:
        self.emit(
            "dashboard", focus=_plain(focus), goals=_plain(goals), moon_note=moon_note
        )

    def help(self) -> None:
        self.emit("help")

    def close(self) -> None:
        self.emit("closed")

    # ------------------------------------------------------ Second Brain e afins
    def context_hits(self, hits: Any) -> None:
        self.emit("context_hits", hits=_plain(hits))

    def search_results(self, hits: Any) -> None:
        self.emit("search_results", hits=_plain(hits))

    def print_note(self, note: Any) -> None:
        self.emit("note", note=_plain(note))

    def note_preview(
        self,
        title: str,
        tags: list[str],
        links: list[str],
        content: str,
        folder: str,
    ) -> None:
        self.emit(
            "note_preview",
            title=title,
            tags=_plain(tags),
            links=_plain(links),
            content=content,
            folder=folder,
        )

    def reminders(self, rows: Any) -> None:
        self.emit("reminders", rows=_plain(rows))

    def watchlist(self, rows: list[dict[str, Any]]) -> None:
        self.emit("watchlist", rows=_plain(rows))

    # ------------------------------------------------------------------- entrada
    def prompt(self, label: str = "Você") -> str:
        """Pergunta ao usuario nunca bloqueia o servidor: devolve vazio."""
        self.emit("prompt", label=label)
        return ""

    def erase_input_line(self, lines: int = 1) -> None:
        self.emit("erase_input", lines=int(lines))
