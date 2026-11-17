from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from zaya.agent.types import AssistantMessage
from zaya.config import Config, SecondBrainConfig
from zaya.memory.dream import (
    Dreamer,
    compose_dream_note,
    dream_recap,
    dream_target_day,
    in_dream_window,
    keywords,
)
from zaya.memory.store import MemoryStore
from zaya.secondbrain.index import SecondBrainIndex
from zaya.secondbrain.notes import Note, SecondBrain


class _FakeClient:
    def __init__(self, sonho: str = "", falhar: bool = False) -> None:
        self.sonho = sonho
        self.falhar = falhar
        self.deleted: list[str] = []
        self.prompts: list[str] = []

    async def create_session(self, title: str | None = None):
        return SimpleNamespace(id="ses_sonho")

    async def prompt_sync(self, session_id: str, text: str, **kwargs):
        self.prompts.append(text)
        if self.falhar:
            raise RuntimeError("provedor indisponível")
        return AssistantMessage(id="msg_sonho", text=self.sonho)

    async def delete_session(self, session_id: str) -> None:
        self.deleted.append(session_id)


@pytest.fixture
def sonho(tmp_path, isolated_env):
    cfg = Config.load(
        config_path=tmp_path / "missing.toml",
        env={"ZAYA_DATA_DIR": str(tmp_path / "data")},
    )
    cfg.memory.use_llm = False
    store = MemoryStore(tmp_path / "zaya.db")
    sb_cfg = SecondBrainConfig(path=str(tmp_path / "brain"), default_folder="Inbox")
    index = SecondBrainIndex(sb_cfg, tmp_path / "sb.db")
    brain = SecondBrain(sb_cfg, index)
    proj = tmp_path / "proj"
    proj.mkdir()
    yield cfg, store, brain, index, proj
    index.close()
    store.close()


def _criar_memoria_dia(brain: SecondBrain, dia: date) -> None:
    brain.create_note(
        f"Memória {dia.isoformat()}",
        "## Síntese do dia\nRevisei o README e falei de parentesco no mestrado.",
        folder="Memoria",
        tags=["memoria", "diario"],
        confirm=True,
    )


def _criar_memoria_antiga(brain: SecondBrain) -> None:
    brain.create_note(
        "Diário de campo",
        "Discussão sobre parentesco e memória na aldeia.",
        folder="Inbox",
        tags=["antropologia"],
        confirm=True,
    )


async def test_dream_escreve_nota_e_nao_repete(sonho):
    cfg, store, brain, index, proj = sonho
    dia = date.today()
    _criar_memoria_dia(brain, dia)
    _criar_memoria_antiga(brain)

    dreamer = Dreamer(cfg, store, brain, index, _FakeClient(), proj)
    caminho = await dreamer.dream(dia)

    assert caminho is not None
    assert caminho.startswith("Sonhos/Sonho ")
    nota = brain.read_note(caminho)
    assert "sonho" in nota.tags
    assert "## Sonho da noite" in nota.body
    # O sonho precisa voltar para a memoria do dia: pasta sozinha nao e grafo.
    assert f"Memória {dia.isoformat()}" in nota.links
    assert store.get_meta(f"memory_dreamed:{dia.isoformat()}") == caminho
    assert await dreamer.dream(dia) is None


async def test_dream_com_modelo(sonho):
    cfg, store, brain, index, proj = sonho
    cfg.memory.use_llm = True
    dia = date.today()
    _criar_memoria_dia(brain, dia)
    _criar_memoria_antiga(brain)

    cliente = _FakeClient(sonho="## Ecos do dia\nUm fio entre o README e a aldeia.")
    dreamer = Dreamer(cfg, store, brain, index, cliente, proj)
    caminho = await dreamer.dream(dia)

    assert caminho is not None
    assert "Um fio entre o README e a aldeia." in brain.read_note(caminho).body
    assert cliente.deleted == ["ses_sonho"]
    assert "Memória do dia" in cliente.prompts[0]


async def test_dream_fallback_quando_modelo_falha(sonho):
    cfg, store, brain, index, proj = sonho
    cfg.memory.use_llm = True
    dia = date.today()
    _criar_memoria_dia(brain, dia)

    dreamer = Dreamer(cfg, store, brain, index, _FakeClient(falhar=True), proj)
    caminho = await dreamer.dream(dia)

    assert caminho is not None
    assert "## Sonho da noite" in brain.read_note(caminho).body


async def test_dream_sem_memoria_do_dia_sonha_com_antigas(sonho):
    cfg, store, brain, index, proj = sonho
    dia = date.today()
    _criar_memoria_antiga(brain)

    dreamer = Dreamer(cfg, store, brain, index, _FakeClient(), proj)
    caminho = await dreamer.dream(dia)

    assert caminho is not None
    assert "Dia sem memória consolidada" in brain.read_note(caminho).body


async def test_sem_nada_nao_sonha(sonho):
    cfg, store, brain, index, proj = sonho
    dreamer = Dreamer(cfg, store, brain, index, _FakeClient(), proj)

    assert await dreamer.dream(date.today()) is None
    assert store.get_meta(f"memory_dreamed:{date.today().isoformat()}") is None


async def test_dream_desabilitado_nao_sonha(sonho):
    cfg, store, brain, index, proj = sonho
    cfg.memory.dream_enabled = False
    _criar_memoria_dia(brain, date.today())

    dreamer = Dreamer(cfg, store, brain, index, _FakeClient(), proj)
    assert await dreamer.dream(date.today()) is None


def test_compose_dream_note_com_modelo_lista_memorias():
    class _Hit:
        title = "Diário de campo"
        rel_path = "Inbox/Diário de campo.md"
        snippet = "parentesco e memória"
        section = ""

        def as_context(self, max_chars=480):
            return f"- **{self.title}** (`{self.rel_path}`): {self.snippet}"

    nota = compose_dream_note(
        date(2026, 11, 16),
        None,
        [_Hit()],  # type: ignore[list-item]
        "## Ecos do dia\nUm fio.",
    )
    assert "Um fio." in nota
    assert "## Memórias revisitadas" in nota
    assert "Diário de campo" in nota


@pytest.mark.parametrize(
    ("hora", "esperado"),
    [
        ((1, 59), False),
        ((2, 0), True),
        ((3, 30), True),
        ((7, 59), True),
        ((8, 0), False),
        ((9, 0), False),
        ((12, 0), False),
        ((23, 0), False),
    ],
)
def test_janela_de_sonho_padrao(hora, esperado):
    agora = datetime(2026, 11, 16, *hora)
    assert in_dream_window(agora, "02:00", "08:00") is esperado


def test_janela_de_sonho_que_vira_a_meia_noite():
    assert in_dream_window(datetime(2026, 11, 16, 23, 45), "23:30", "08:00") is True
    assert in_dream_window(datetime(2026, 11, 17, 3, 0), "23:30", "08:00") is True
    assert in_dream_window(datetime(2026, 11, 16, 22, 0), "23:30", "08:00") is False
    assert in_dream_window(datetime(2026, 11, 16, 9, 0), "23:30", "08:00") is False


def test_horario_invalido_nao_quebra():
    assert in_dream_window(datetime(2026, 11, 16, 3, 0), "lixo", "08:00") is True


@pytest.mark.parametrize(
    ("hora", "dia_esperado"),
    [
        ((3, 0), date(2026, 11, 15)),
        ((11, 59), date(2026, 11, 15)),
        ((12, 0), date(2026, 11, 16)),
        ((23, 50), date(2026, 11, 16)),
    ],
)
def test_dia_alvo_do_sonho(hora, dia_esperado):
    assert dream_target_day(datetime(2026, 11, 16, *hora)) == dia_esperado


def test_keywords_descarta_stopwords_e_palavras_curtas():
    assert keywords("bom dia, ok! falei de parentesco no mestrado") == [
        "parentesco",
        "mestrado",
    ]


def test_keywords_limita_a_quantidade():
    texto = " ".join(f"palavra{i}" for i in range(40))
    assert len(keywords(texto, limit=5)) == 5


async def test_dream_nao_repete_nota_antiga(sonho):
    """A busca e por chunk: sem limite, uma nota longa ocupava varias vagas."""
    cfg, store, brain, index, proj = sonho
    dia = date.today()
    _criar_memoria_dia(brain, dia)
    for titulo in ("Diário de campo", "Nota sobre Lévi-Strauss", "Ensaio etnicidade"):
        brain.create_note(
            titulo,
            "parentesco memoria etnicidade alteridade parentesco memoria "
            "etnicidade alteridade parentesco memoria etnicidade",
            folder="Inbox",
            confirm=True,
        )

    dreamer = Dreamer(cfg, store, brain, index, _FakeClient(), proj)
    day_note = dreamer._day_note(dia)
    antigas = dreamer._old_memories(dia, day_note)

    por_nota: dict[str, int] = {}
    for hit in antigas:
        por_nota[hit.rel_path] = por_nota.get(hit.rel_path, 0) + 1
    assert max(por_nota.values()) <= 2
    assert len(por_nota) >= 2, antigas


async def test_dream_ignora_sonhos_anteriores_e_a_si_mesmo(sonho):
    cfg, store, brain, index, proj = sonho
    dia = date.today()
    _criar_memoria_dia(brain, dia)
    brain.create_note(
        "Sonho " + (dia.replace(day=1)).isoformat(),
        "parentesco memoria noite anterior",
        folder="Sonhos",
        confirm=True,
    )

    dreamer = Dreamer(cfg, store, brain, index, _FakeClient(), proj)
    antigas = dreamer._old_memories(dia, dreamer._day_note(dia))

    assert all("Sonho " not in hit.rel_path for hit in antigas)
    assert all(not hit.rel_path.startswith("Sonhos/") for hit in antigas)


def test_dream_recap_usa_frase_e_pergunta():
    nota = Note(
        path=Path("/tmp/x.md"),
        rel_path="Sonhos/Sonho 2026-11-15.md",
        title="Sonho 2026-11-15",
        meta={},
        body=(
            "# Sonho 2026-11-15\n\n"
            "A madrugada costura fios soltos. Havia um teclado quebrado.\n\n"
            "## Ecos do dia\necos\n\n"
            "## Ressonâncias\nressonancias\n\n"
            "## Pergunta para amanhã\nO que fica aqui? Talvez nada.\n"
        ),
        tags=["sonho"],
        links=[],
    )
    aviso = dream_recap(date(2026, 11, 15), nota)
    assert aviso.startswith("Sonhei com 15/11")
    assert "A madrugada costura fios soltos." in aviso
    assert "Ficou a pergunta: O que fica aqui?" in aviso
    assert "Ressonâncias" not in aviso
    assert "…." not in aviso
    assert len(aviso) <= 381


def test_dream_recap_sem_secoes_nao_quebra():
    nota = Note(
        path=Path("/tmp/x.md"),
        rel_path="Sonhos/x.md",
        title="x",
        meta={},
        body="# x\n",
        tags=[],
        links=[],
    )
    assert dream_recap(date(2026, 11, 15), nota) == ""
