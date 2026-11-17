"""Sonho noturno: costura a memoria do dia com as memorias antigas.

Enquanto a consolidacao registra "o que aconteceu", o sonho e a passagem
noturna e ociosa que reconta o dia por associacao com o que a Zaya ja viveu.
A nota gerada vive no Second Brain (Markdown + FTS5) e volta automaticamente
como contexto nos dias seguintes.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from zaya.agent import AgentClient
from zaya.config import Config
from zaya.memory.store import MemoryStore
from zaya.secondbrain.index import SearchHit, SecondBrainIndex
from zaya.secondbrain.notes import Note, SecondBrain

_DREAM_PROMPT = (
    "Você é a Zaya, a agente pessoal da Talita. É madrugada e você está "
    "sonhando. Escreva um sonho em Markdown, em português do Brasil, que "
    "costure a memória do dia com as memórias antigas que receber. Seja "
    "poética, mas fiel: não invente fatos, não use ferramentas e não leia "
    "arquivos. Comece com um parágrafo onírico, sem título. Depois use "
    "exatamente estas seções:\n"
    "## Ecos do dia\n"
    "## Ressonâncias\n"
    "## Pergunta para amanhã\n\n"
    "Máximo de 300 palavras.\n\n---\n\n"
)


def _strip_dream_heading(text: str) -> str:
    """Remove um "## Sonho" inicial para nao duplicar o titulo da nota."""
    text = text.strip()
    return re.sub(r"^#{1,3}\s*[Ss]onho[^\n]*\n+", "", text, flags=re.IGNORECASE).strip()


def _minutes(hhmm: str, default: str = "00:00") -> int:
    """Converte "HH:MM" em minutos desde a meia-noite (tolerante a lixo)."""
    for value in (hhmm, default):
        match = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(value or ""))
        if not match:
            continue
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute
    return 0


def in_dream_window(now: datetime, start: str, end: str) -> bool:
    """Diz se ``now`` cai na janela noturna ``[start, end)``.

    A janela pode virar a meia-noite (ex.: 23:30 -> 08:00), que e o caso
    normal de ``dream_at = "02:00"``: o sonho acontece de madrugada.
    """
    begin, finish = _minutes(start), _minutes(end, "08:00")
    current = now.hour * 60 + now.minute
    if begin == finish:
        return True
    if begin < finish:
        return begin <= current < finish
    return current >= begin or current < finish


def dream_target_day(now: datetime) -> date:
    """Dia sobre o qual se sonha: o ultimo dia que fechou.

    De madrugada (antes do meio-dia) o dia fechado e ontem; a noite ainda em
    curso sonha sobre hoje. O corte no meio-dia evita que uma checagem perdida
    faca a Zaya sonhar sobre um dia vazio.
    """
    today = now.date()
    return today - timedelta(days=1) if now.hour < 12 else today


def _excerpt(text: str, limit: int = 600) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    if len(value) > limit:
        value = value[:limit].rstrip() + "…"
    return value


def _fallback_dream(day: date, day_note: Note | None, old: list[SearchHit]) -> str:
    """Sonho deterministico, usado quando o modelo falha ou esta desligado."""
    sections = [
        "## Sonho da noite\n"
        f"Na madrugada de {day.isoformat()}, a Zaya sonhou com o que viveu e "
        f"com {len(old)} memória(s) antiga(s)."
    ]
    if day_note:
        sections.append("## Ecos do dia\n" + _excerpt(day_note.body))
    else:
        sections.append("## Ecos do dia\nDia sem memória consolidada.")
    if old:
        sections.append(
            "## Ressonâncias\n" + "\n".join(hit.as_context(320) for hit in old)
        )
    sections.append("## Pergunta para amanhã\nO que esse sonho quer dizer?")
    return "\n\n".join(sections)


def compose_dream_note(
    day: date,
    day_note: Note | None,
    old: list[SearchHit],
    dream_text: str = "",
) -> str:
    """Monta o corpo Markdown da nota de sonho."""
    if dream_text:
        parts = [_strip_dream_heading(dream_text)]
        if old:
            parts.append(
                "## Memórias revisitadas\n"
                + "\n".join(hit.as_context(240) for hit in old)
            )
    else:
        parts = [_fallback_dream(day, day_note, old)]
    return "\n\n".join(parts).strip() + "\n"


class Dreamer:
    """Escreve a nota de sonho cruzando o dia com memorias antigas."""

    def __init__(
        self,
        cfg: Config,
        store: MemoryStore,
        brain: SecondBrain,
        index: SecondBrainIndex,
        client: AgentClient,
        directory: Path,
        logger: logging.Logger | None = None,
    ):
        self.cfg = cfg
        self.store = store
        self.brain = brain
        self.index = index
        self.client = client
        self.directory = directory
        self.logger = logger or logging.getLogger(__name__)
        self._running = False

    async def dream(
        self,
        day: date | None = None,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> str | None:
        cfg = self.cfg.memory
        if not cfg.dream_enabled or self._running:
            return None
        day = day or datetime.now().astimezone().date()
        key = f"memory_dreamed:{day.isoformat()}"
        if not force and self.store.get_meta(key):
            return None

        day_note = await asyncio.to_thread(self._day_note, day)
        old = await asyncio.to_thread(self._old_memories, day, day_note)
        if day_note is None and not old:
            return None

        self._running = True
        try:
            dream_text = ""
            if cfg.use_llm:
                dream_text = await self._dream(
                    day, day_note, old, timeout_seconds or cfg.llm_timeout_seconds
                )
            body = compose_dream_note(day, day_note, old, dream_text)
            note = await asyncio.to_thread(self._write_note, day, body)
            self.store.set_meta(key, note.rel_path)
            self.store.record_note(None, "dream", note.rel_path, note.title)
            return note.rel_path
        finally:
            self._running = False

    def _day_note(self, day: date) -> Note | None:
        name = f"{self.cfg.memory.folder}/Memória {day.isoformat()}"
        path = self.brain.resolve(name)
        if path is None:
            return None
        return self.brain.read_note(path)

    def _old_memories(
        self, day: date, day_note: Note | None, limit: int = 5
    ) -> list[SearchHit]:
        day_rel = day_note.rel_path if day_note else None
        dream_prefix = f"{self.cfg.memory.dream_folder}/Sonho "
        hits: list[SearchHit] = []
        seen: set[str] = set()

        def relevant(rel_path: str) -> bool:
            if rel_path == day_rel or rel_path.startswith(dream_prefix):
                return False
            return rel_path not in seen

        if day_note:
            query = f"{day_note.title} {day_note.body[:2000]}"
            for hit in self.index.search(query, limit=limit * 2):
                if not relevant(hit.rel_path):
                    continue
                seen.add(hit.rel_path)
                hits.append(hit)
        for hit in self.index.recent(limit=limit * 2):
            if not relevant(hit.rel_path):
                continue
            seen.add(hit.rel_path)
            hits.append(hit)
            if len(hits) >= limit:
                break
        return hits[:limit]

    def _dream_context(self, day: date, day_note: Note | None, old: list[SearchHit]) -> str:
        parts: list[str] = []
        if day_note:
            parts.append("## Memória do dia\n\n" + _excerpt(day_note.body, 4000))
        else:
            parts.append("## Memória do dia\n\n(sem memória consolidada hoje)")
        if old:
            parts.append(
                "## Memórias antigas\n\n" + "\n".join(hit.as_context(480) for hit in old)
            )
        return "\n\n".join(parts)

    async def _dream(
        self,
        day: date,
        day_note: Note | None,
        old: list[SearchHit],
        timeout_seconds: float,
    ) -> str:
        context = self._dream_context(day, day_note, old)
        if not context:
            return ""
        session_id = ""
        try:
            session = await self.client.create_session(title=f"sonho {day.isoformat()}")
            session_id = session.id
            message = await asyncio.wait_for(
                self.client.prompt_sync(session_id, _DREAM_PROMPT + context),
                timeout=max(10.0, timeout_seconds),
            )
            return message.text.strip()
        except Exception as exc:  # pragma: no cover - depende do provedor
            self.logger.warning("sonho pelo modelo falhou: %s", exc)
            return ""
        finally:
            if session_id:
                await self.client.delete_session(session_id)

    def _write_note(self, day: date, body: str) -> Note:
        cfg = self.cfg.memory
        title = f"Sonho {day.isoformat()}"
        tags = ["sonho", "memoria", day.isoformat()]
        existing = self.brain.resolve(f"{cfg.dream_folder}/{title}")
        if existing:
            return self.brain.update_note(str(existing), body, confirm=True, tags=tags)
        return self.brain.create_note(
            title, body, folder=cfg.dream_folder, tags=tags, confirm=True
        )
