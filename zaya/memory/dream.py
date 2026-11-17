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
from zaya.secondbrain.notes import Note, NoteConflict, SecondBrain

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
    "Se houver sonhos anteriores no material, retome os fios deles — o que "
    "voltou, o que mudou — sem repetir as mesmas palavras.\n\n"
    "Máximo de 300 palavras.\n\n---\n\n"
)

# Palavras que aparecem em toda conversa e nao dizem nada sobre ressonancia.
# Sem esse filtro, "bom dia" no resumo do dia puxava qualquer nota que tivesse
# um "dia" e o sonho virava sorteio.
_STOPWORDS = frozenset(
    """
    a à ao aos as até com como da das de do dos e é eh ela ele eles em entre era
    essa esse esta este eu foi há isso isto já lhe mais mas me mesmo meu minha na
    nas não no nos o os ou para pela pelo por porque que quem se sem ser seu sua
    são só também te tem ter tu um uma você vocês vou vai dei dia hoje hoje ontem
    agora bom boa oi olá ok certo aqui ali então ainda muito pouco tudo nada
    fazer fiz falar falei sobre isso aquilo essa esse tipo coisa gente
    """.split()
)

_SHORT_RE = re.compile(r"\w+", flags=re.UNICODE)

# Trechos por nota: o indice e por chunk, entao uma nota longa podia ocupar
# varias vagas e o sonho revisitava um dia so.
_PER_NOTE = 2


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
    # Sem marcadores de titulo: um "# Memória ..." colado dentro de uma secao
    # quebrava o parser e fazia a nota parecer sem estrutura.
    sem_titulos = re.sub(r"(?m)^#{1,6}\s*", "", text or "")
    value = re.sub(r"\s+", " ", sem_titulos).strip()
    if len(value) > limit:
        value = value[:limit].rstrip() + "…"
    return value


def keywords(text: str, limit: int = 24) -> list[str]:
    """Tokens que valem uma busca: sem stopword, sem palavra de 1-2 letras."""
    found: list[str] = []
    seen: set[str] = set()
    for token in _SHORT_RE.findall(text or ""):
        key = token.casefold()
        if len(key) < 3 or key in _STOPWORDS or key in seen:
            continue
        seen.add(key)
        found.append(token)
        if len(found) >= limit:
            break
    return found


def first_sentence(text: str, limit: int = 220) -> str:
    """Primeira frase de um trecho, para caber num aviso de uma linha."""
    value = re.sub(r"\s+", " ", text or "").strip()
    if not value:
        return ""
    match = re.match(r"^(.+?[.!?])(?:\s|$)", value)
    sentence = match.group(1) if match else value
    return _excerpt(sentence, limit)


# Seccoes que a nota de sonho precisa ter para virar aviso e leitura.
_DREAM_SECTIONS = ("Ecos do dia", "Ressonâncias", "Pergunta para amanhã")

# Pergunta de reserva quando o modelo nao entrega uma: generica, mas honesta.
_FALLBACK_QUESTION = "O que desse sonho ainda vai fazer sentido daqui a uma semana?"


def _section(body: str, title: str) -> str:
    """Conteudo de uma secao ``## <title>`` ate a proxima secao."""
    # Termina so em titulo de nivel 1-2: um "# algo" dentro do conteudo (trecho
    # de outra nota) nao pode zerar a secao.
    pattern = re.compile(
        rf"^#{{1,2}}\s*{re.escape(title)}\s*$(.*?)(?=^#{{1,2}}\s|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(body or "")
    return match.group(1).strip() if match else ""


def dream_recap(day: date, note: Note) -> str:
    """Aviso de uma linha sobre o sonho, para o dia seguinte.

    Usa o proprio texto do sonho: a primeira frase do paragrafo onirico e a
    pergunta que ficou. Nada de segunda chamada ao modelo: o aviso e barato e
    nunca inventa.
    """
    body = note.body or ""
    onirico = body.split("\n## ", 1)[0]
    onirico = re.sub(r"^#\s+.*$", "", onirico, count=1, flags=re.MULTILINE)
    frase = first_sentence(onirico, 200)
    pergunta = first_sentence(_section(body, "Pergunta para amanhã"), 160)
    if not frase and not pergunta:
        return ""
    frase = frase.rstrip(".…").rstrip()
    pergunta = pergunta.strip()
    partes = [f"Sonhei com {day.strftime('%d/%m')}"]
    if frase:
        partes.append(f": {frase}.")
    else:
        partes.append(".")
    if pergunta:
        partes.append(f" Ficou a pergunta: {pergunta}")
    aviso = "".join(partes).strip()
    # Um aviso curto: o resto esta na nota.
    if len(aviso) > 380:
        aviso = aviso[:377].rstrip() + "…"
    return aviso


def _fallback_dream(
    day: date,
    day_note: Note | None,
    old: list[SearchHit],
    opening: str = "",
) -> str:
    """Sonho deterministico, usado quando o modelo falha, esta desligado ou
    devolve algo sem a estrutura pedida.

    ``opening`` preserva a prosa do modelo quando ela existe e so a moldura
    (secoes) precisa ser reconstruida: o texto onirico e a parte que vale.
    """
    sections = [
        opening.strip()
        or (
            "## Sonho da noite\n"
            f"Na madrugada de {day.isoformat()}, a Zaya sonhou com o que viveu e "
            f"com {len(old)} memória(s) antiga(s)."
        )
    ]
    if day_note:
        sections.append("## Ecos do dia\n" + _excerpt(day_note.body))
    else:
        sections.append("## Ecos do dia\nDia sem memória consolidada.")
    sections.append(
        "## Ressonâncias\n"
        + (
            "\n".join(hit.as_context(320) for hit in old)
            if old
            else "Nada antigo ressoou hoje."
        )
    )
    sections.append(f"## Pergunta para amanhã\n{_FALLBACK_QUESTION}")
    return "\n\n".join(sections)


def has_structure(text: str) -> bool:
    """Diz se o texto do modelo tem as tres secoes que a nota promete.

    Conta o *titulo*, nao o conteudo: uma secao vazia e legitima ("nada
    antigo ressoou hoje"), mas a falta da secao significa que o modelo
    ignorou o formato - e aí o aviso do dia seguinte sairia vazio.
    """
    return all(
        re.search(rf"^#{{1,2}}\s*{re.escape(title)}\s*$", text or "", re.IGNORECASE | re.MULTILINE)
        for title in _DREAM_SECTIONS
    )


def compose_dream_note(
    day: date,
    day_note: Note | None,
    old: list[SearchHit],
    dream_text: str = "",
) -> str:
    """Monta o corpo Markdown da nota de sonho."""
    text = _strip_dream_heading(dream_text)
    if text and has_structure(text):
        parts = [text]
        if old:
            parts.append(
                "## Memórias revisitadas\n"
                + "\n".join(hit.as_context(240) for hit in old)
            )
        return "\n\n".join(parts).strip() + "\n"
    # Sem estrutura: mantem a prosa (se houver) e reconstroi a moldura.
    return _fallback_dream(day, day_note, old, opening=text).strip() + "\n"


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
        forgotten = f"memory_dream_forgotten:{day.isoformat()}"
        # ``forgotten`` e o pedido explicito de esquecer: sem ele, descartar um
        # sonho faria o catch-up da manha sonhar o mesmo dia de novo.
        if not force and (self.store.get_meta(key) or self.store.get_meta(forgotten)):
            return None

        day_note = await asyncio.to_thread(self._day_note, day)
        old = await asyncio.to_thread(self._old_memories, day, day_note)
        previous = await asyncio.to_thread(self._previous_dreams, day)
        if day_note is None and not old:
            return None

        self._running = True
        try:
            dream_text = ""
            if cfg.use_llm:
                dream_text = await self._dream(
                    day,
                    day_note,
                    old,
                    previous,
                    timeout_seconds or cfg.llm_timeout_seconds,
                )
                if dream_text and not has_structure(_strip_dream_heading(dream_text)):
                    self.logger.warning(
                        "sonho de %s veio fora do formato; refazendo a moldura",
                        day.isoformat(),
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
        """Memorias antigas que ressoam com o dia, sem repetir nota.

        A busca do indice e por chunk e um OR de tokens: sem estes limites, as
        notas de memoria diaria (longas e recentes) ocupavam as cinco vagas e o
        sonho so conversava com a semana passada. Entao: no maximo
        ``_PER_NOTE`` trechos por nota, e fora sonhos, a nota do dia e a propria
        nota que esta sendo escrita.
        """
        day_rel = day_note.rel_path if day_note else None
        dream_prefix = f"{self.cfg.memory.dream_folder}/Sonho "
        hits: list[SearchHit] = []
        seen: set[str] = set()
        per_note: dict[str, int] = {}
        self_rel = f"{self.cfg.memory.dream_folder}/Sonho {day.isoformat()}.md"

        def relevant(rel_path: str) -> bool:
            if rel_path in {day_rel, self_rel} or rel_path.startswith(dream_prefix):
                return False
            return per_note.get(rel_path, 0) < _PER_NOTE

        def take(hit: SearchHit) -> None:
            per_note[hit.rel_path] = per_note.get(hit.rel_path, 0) + 1
            hits.append(hit)
            seen.add(hit.rel_path)

        if day_note:
            texto = f"{day_note.title} {day_note.body[:2000]}"
            # Duas buscas, uma depois da outra: a semantica acha sinonimo
            # ("impulsao" x "vontade"), a lexical acha termo exato que o vetor
            # borra (nome, sigla, titulo). Uma completa a outra.
            for candidato in self._candidates(texto, limit):
                if not relevant(candidato.rel_path):
                    continue
                take(candidato)
                if len(hits) >= limit:
                    return hits
        for hit in self.index.recent(limit=limit * 3):
            if hit.rel_path in seen or not relevant(hit.rel_path):
                continue
            take(hit)
            if len(hits) >= limit:
                break
        return hits[:limit]

    def _candidates(self, texto: str, limit: int) -> list[SearchHit]:
        """Semantico primeiro, lexical completando - nessa ordem, sem repetir."""
        vistos: set[str] = set()
        saida: list[SearchHit] = []
        semantico = self.index.semantic_search(
            texto, limit=limit * 3, min_score=self.cfg.secondbrain.embed_min_score
        )
        query = " ".join(keywords(texto))
        lexical = self.index.search(query, limit=limit * 3) if query else []
        for hit in list(semantico) + list(lexical):
            chave = f"{hit.rel_path}#{hit.section}"
            if chave in vistos:
                continue
            vistos.add(chave)
            saida.append(hit)
        return saida

    def _previous_dreams(self, day: date, limit: int = 2) -> list[Note]:
        """Sonhos das noites anteriores: a continuidade que faltava.

        Ficam fora de ``_old_memories`` de proposito (nao entram como
        "memorias revisitadas"), mas entram no material como fio narrativo.
        """
        prefix = f"{self.cfg.memory.dream_folder}/Sonho "
        found: list[tuple[date, str]] = []
        for hit in self.index.recent(limit=60):
            if not hit.rel_path.startswith(prefix):
                continue
            match = re.search(r"(\d{4}-\d{2}-\d{2})", hit.rel_path)
            if not match:
                continue
            try:
                when = date.fromisoformat(match.group(1))
            except ValueError:
                continue
            if when >= day:
                continue
            found.append((when, hit.rel_path))
        found.sort(reverse=True)
        notes: list[Note] = []
        for _when, rel_path in found[:limit]:
            try:
                notes.append(self.brain.read_note(rel_path))
            except Exception as exc:  # pragma: no cover - nota sumiu no meio
                self.logger.warning("sonho: nao consegui ler %s: %s", rel_path, exc)
        return notes

    def _dream_context(
        self,
        day: date,
        day_note: Note | None,
        old: list[SearchHit],
        previous: list[Note] | None = None,
    ) -> str:
        parts: list[str] = []
        if day_note:
            parts.append("## Memória do dia\n\n" + _excerpt(day_note.body, 4000))
        else:
            parts.append("## Memória do dia\n\n(sem memória consolidada hoje)")
        if old:
            parts.append(
                "## Memórias antigas\n\n" + "\n".join(hit.as_context(480) for hit in old)
            )
        if previous:
            parts.append(
                "## Sonhos anteriores\n\n"
                + "\n\n".join(
                    f"### {note.title}\n{_excerpt(note.body, 1200)}"
                    for note in previous
                )
            )
        return "\n\n".join(parts)

    async def _dream(
        self,
        day: date,
        day_note: Note | None,
        old: list[SearchHit],
        previous: list[Note] | None,
        timeout_seconds: float,
    ) -> str:
        context = self._dream_context(day, day_note, old, previous)
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
        # Liga o sonho a memoria do dia: o Second Brain e um grafo, nao uma
        # pasta. Sem o link, a nota do dia nunca "sabe" que sonhou com ela.
        links = [f"Memória {day.isoformat()}"] if self._day_note(day) else []
        try:
            return self.brain.create_note(
                title,
                body,
                folder=cfg.dream_folder,
                tags=tags,
                links=links,
                confirm=True,
            )
        except NoteConflict:
            # TUI e servico do Telegram podem sonhar o mesmo dia ao mesmo
            # tempo: quem chegar depois adota a nota de quem ganhou.
            existing = self.brain.resolve(f"{cfg.dream_folder}/{title}")
            if existing is None:
                raise
            self.logger.info("sonho de %s ja existia; adotando a nota", day.isoformat())
            return self.brain.read_note(existing)
