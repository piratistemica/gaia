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
    has_structure,
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

    completo = (
        "A madrugada costura.\n\n"
        "## Ecos do dia\nUm fio.\n\n"
        "## Ressonâncias\nUma ressonância.\n\n"
        "## Pergunta para amanhã\nO que fica?"
    )
    nota = compose_dream_note(
        date(2026, 11, 16),
        None,
        [_Hit()],  # type: ignore[list-item]
        completo,
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


def test_has_structure_exige_as_tres_secoes():
    completo = (
        "Abertura.\n\n## Ecos do dia\na\n\n## Ressonâncias\nb\n\n"
        "## Pergunta para amanhã\nc"
    )
    assert has_structure(completo) is True
    assert has_structure("Abertura.\n\n## Ecos do dia\na") is False
    assert has_structure("") is False


def test_compose_refaz_a_moldura_mantendo_a_prosa_do_modelo():
    """Modelo fora do formato nao perde o texto: ganha as secoes que faltam."""
    nota = compose_dream_note(
        date(2026, 11, 16),
        None,
        [],
        "# Sonho\n\nA madrugada virou fita e a fita virou reza.",
    )
    assert "A madrugada virou fita e a fita virou reza." in nota
    assert "## Ecos do dia" in nota
    assert "## Ressonâncias" in nota or "## Pergunta para amanhã" in nota
    assert has_structure(nota) is True
    assert nota.count("A madrugada virou fita") == 1


async def test_modelo_fora_do_formato_ainda_gera_aviso(sonho):
    cfg, store, brain, index, proj = sonho
    cfg.memory.use_llm = True
    dia = date.today()
    _criar_memoria_dia(brain, dia)
    cliente = _FakeClient(sonho="Sonhei com uma fita que virou reza.")

    dreamer = Dreamer(cfg, store, brain, index, cliente, proj)
    caminho = await dreamer.dream(dia)

    nota = brain.read_note(caminho)
    assert has_structure(nota.body)
    assert dream_recap(dia, nota).startswith("Sonhei com")
    assert "fita que virou reza" in dream_recap(dia, nota)


def test_sonhos_anteriores_entram_como_continuidade(sonho):
    cfg, store, brain, index, proj = sonho
    dia = date(2026, 11, 16)
    _criar_memoria_dia(brain, dia)
    brain.create_note(
        "Sonho 2026-11-14",
        "A madrugada tinha cheiro de arquivo.\n\n## Pergunta para amanhã\nO que fica?",
        folder="Sonhos",
        confirm=True,
    )
    dreamer = Dreamer(cfg, store, brain, index, _FakeClient(), proj)

    anteriores = dreamer._previous_dreams(dia)

    assert [n.title for n in anteriores] == ["Sonho 2026-11-14"]


def test_sonho_do_proprio_dia_nao_conta_como_anterior(sonho):
    cfg, store, brain, index, proj = sonho
    dia = date(2026, 11, 16)
    brain.create_note("Sonho 2026-11-16", "hoje", folder="Sonhos", confirm=True)
    brain.create_note("Sonho 2026-11-17", "amanha", folder="Sonhos", confirm=True)
    dreamer = Dreamer(cfg, store, brain, index, _FakeClient(), proj)

    assert dreamer._previous_dreams(dia) == []


async def test_sonho_anterior_aparece_no_contexto_do_modelo(sonho):
    cfg, store, brain, index, proj = sonho
    cfg.memory.use_llm = True
    dia = date(2026, 11, 16)
    _criar_memoria_dia(brain, dia)
    brain.create_note(
        "Sonho 2026-11-15",
        "Um teclado quebrado e um dedo cortado.",
        folder="Sonhos",
        confirm=True,
    )
    cliente = _FakeClient(sonho="A madrugada voltou ao teclado.")
    dreamer = Dreamer(cfg, store, brain, index, cliente, proj)

    await dreamer.dream(dia)

    assert "## Sonhos anteriores" in cliente.prompts[0]
    assert "teclado quebrado" in cliente.prompts[0]


async def test_descartar_marca_o_dia_e_nao_sonha_de_novo(sonho):
    cfg, store, brain, index, proj = sonho
    dia = date.today()
    _criar_memoria_dia(brain, dia)
    dreamer = Dreamer(cfg, store, brain, index, _FakeClient(), proj)
    caminho = await dreamer.dream(dia)
    assert caminho is not None

    # Simula o descarte: nota fora, marcas trocadas.
    brain.delete_note(caminho, confirm=True)
    store.delete_meta(f"memory_dreamed:{dia.isoformat()}")
    store.set_meta(f"memory_dream_forgotten:{dia.isoformat()}", "2026-11-16T09:00:00")

    assert await dreamer.dream(dia) is None
    # force continua podendo, para quem quiser refazer de proposito.
    assert await dreamer.dream(dia, force=True) is not None


async def test_corrida_entre_processos_adota_a_nota_existente(sonho):
    """TUI e servico do Telegram podem sonhar o mesmo dia ao mesmo tempo."""
    from zaya.secondbrain.notes import NoteConflict

    cfg, store, brain, index, proj = sonho
    dia = date.today()
    _criar_memoria_dia(brain, dia)

    class _RaceBrain:
        """resolve() nao ve a nota; create_note() bate no conflito."""

        def __init__(self, real, rel_path):
            self._real = real
            self._rel_path = rel_path
            self._wins = 0

        def resolve(self, name):
            if "Sonho " in str(name):
                self._wins += 1
                return None if self._wins == 1 else self._real.root / self._rel_path
            return self._real.resolve(name)

        def create_note(self, *args, **kwargs):
            raise NoteConflict("ja existe uma nota")

        def read_note(self, name):
            return self._real.read_note(name)

    rel = f"Sonhos/Sonho {dia.isoformat()}.md"
    brain.create_note(
        f"Sonho {dia.isoformat()}",
        "Nota de quem ganhou a corrida.\n\n## Ecos do dia\na\n\n"
        "## Ressonâncias\nb\n\n## Pergunta para amanhã\nO que fica?",
        folder="Sonhos",
        confirm=True,
    )
    dreamer = Dreamer(cfg, store, brain, index, _FakeClient(), proj)
    dreamer.brain = _RaceBrain(brain, rel)  # type: ignore[assignment]

    caminho = await dreamer.dream(dia)

    assert caminho == rel
    assert "ganhou a corrida" in brain.read_note(caminho).body


class _FakeEmbedder:
    """Duplo de embedder: vetores deterministicos por palavra-chave."""

    enabled = True
    model = "fake-embed"

    def __init__(self) -> None:
        self.chamadas: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.chamadas.append(list(texts))
        vetores = []
        for text in texts:
            baixo = (text or "").lower()
            # "vontade" e "impulsao" caem no mesmo eixo: sinonimo aproximado.
            querer = 1.0 if ("vontade" in baixo or "impuls" in baixo) else 0.0
            agenda = 1.0 if "agenda" in baixo else 0.0
            vetores.append([querer, agenda, 0.1])
        return vetores


def test_busca_semantica_encontra_sinonimo_que_a_lexical_perde(tmp_path):
    from zaya.secondbrain.index import SecondBrainIndex

    sb_cfg = SecondBrainConfig(path=str(tmp_path / "brain"))
    embedder = _FakeEmbedder()
    index = SecondBrainIndex(sb_cfg, tmp_path / "sb.db", embedder=embedder)
    try:
        raiz = tmp_path / "brain"
        (raiz / "Inbox").mkdir(parents=True)
        antiga = raiz / "Inbox" / "Vontade.md"
        antiga.write_text("## Notas\nminha vontade de mudar de area", encoding="utf-8")
        index.index_note(antiga)

        # Lexical: "impulsao" nao casa com "vontade".
        assert index.search("impulsao", limit=3) == []
        # Semantico: casa, porque o embedder coloca as duas no mesmo eixo.
        hits = index.semantic_search("impulsao de mudar", limit=3, min_score=0.2)
        assert [h.rel_path for h in hits] == ["Inbox/Vontade.md"]
    finally:
        index.close()


def test_index_sem_embedder_nao_cria_vetores(tmp_path):
    from zaya.secondbrain.index import SecondBrainIndex

    sb_cfg = SecondBrainConfig(path=str(tmp_path / "brain"))
    index = SecondBrainIndex(sb_cfg, tmp_path / "sb.db")
    try:
        raiz = tmp_path / "brain"
        raiz.mkdir(parents=True, exist_ok=True)
        nota = raiz / "a.md"
        nota.write_text("## X\ntexto", encoding="utf-8")
        index.index_note(nota)
        assert index.vectors_pending() == 0
        assert index.semantic_search("texto") == []
    finally:
        index.close()


def test_backfill_embeda_chunks_antigos(tmp_path):
    from zaya.secondbrain.index import SecondBrainIndex

    sb_cfg = SecondBrainConfig(path=str(tmp_path / "brain"))
    raiz = tmp_path / "brain"
    raiz.mkdir(parents=True)
    nota = raiz / "a.md"
    nota.write_text("## X\nquero mudar", encoding="utf-8")

    sem = SecondBrainIndex(sb_cfg, tmp_path / "sb.db")
    sem.index_note(nota)
    sem.close()

    embedder = _FakeEmbedder()
    com = SecondBrainIndex(sb_cfg, tmp_path / "sb.db", embedder=embedder)
    try:
        assert com.vectors_pending() > 0
        assert com.embed_missing() > 0
        assert com.vectors_pending() == 0
        assert len(com.semantic_search("vontade", limit=3)) == 1
    finally:
        com.close()


def test_cosine_e_pack_unpack():
    from zaya.secondbrain.embeddings import cosine, pack, unpack

    vetor = [0.5, -0.25, 1.0]
    assert unpack(pack(vetor)) == vetor
    assert abs(cosine([1, 0], [1, 0]) - 1.0) < 1e-9
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([], [1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_embedder_desligado_sem_modelo():
    from zaya.secondbrain.embeddings import build_embedder

    assert build_embedder("").enabled is False
    assert build_embedder("  ").enabled is False
    ligado = build_embedder("text-embedding-3-small", "https://x/v1", "k")
    assert ligado.enabled is True
    assert ligado.model == "text-embedding-3-small"


async def test_sonho_prefere_busca_semantica(sonho, tmp_path):
    """Com embedder, o sonho acha nota antiga por sentido, nao por palavra."""
    from zaya.secondbrain.index import SecondBrainIndex

    cfg, store, brain, _index, proj = sonho
    dia = date.today()
    _criar_memoria_dia(brain, dia)

    raiz = tmp_path / "brain"
    _criar_memoria_antiga(brain)
    # Mesmo brain do fixture, mas com embedder e indice proprio.
    sb_cfg = SecondBrainConfig(path=str(raiz), default_folder="Inbox")
    semantico = SecondBrainIndex(sb_cfg, tmp_path / "sb_sem.db", embedder=_FakeEmbedder())
    try:
        for nota in raiz.rglob("*.md"):
            semantico.index_note(nota)
        dreamer = Dreamer(cfg, store, brain, semantico, _FakeClient(), proj)
        antigas = dreamer._old_memories(dia, dreamer._day_note(dia))
        assert antigas, "a busca semantica deveria trazer a nota antiga"
        assert any("Diário de campo" in hit.title for hit in antigas)
    finally:
        semantico.close()


def test_limiar_de_ressonancia_vem_da_config(tmp_path):
    """bge-m3 da ~0.45 entre frases sem relacao: 0.2 deixava tudo passar."""
    from zaya.secondbrain.index import SecondBrainIndex

    class _Raso(_FakeEmbedder):
        def embed(self, texts):
            # Eixos diferentes por assunto: cosseno 0, abaixo de qualquer piso.
            return [
                [1.0, 0.0, 0.0] if "outra" in (t or "").lower() else [0.0, 1.0, 0.0]
                for t in texts
            ]

    cfg = Config.load(
        config_path=tmp_path / "missing.toml",
        env={"ZAYA_DATA_DIR": str(tmp_path / "data")},
    )
    assert cfg.secondbrain.embed_min_score == 0.35

    raiz = tmp_path / "brain"
    (raiz / "Inbox").mkdir(parents=True)
    nota = raiz / "Inbox" / "a.md"
    nota.write_text("## X\nconteudo qualquer", encoding="utf-8")
    sb_cfg = SecondBrainConfig(path=str(raiz), default_folder="Inbox")
    index = SecondBrainIndex(sb_cfg, tmp_path / "sb.db", embedder=_Raso())
    try:
        index.index_note(nota)
        assert index.semantic_search("outra coisa", min_score=0.35) == []
    finally:
        index.close()
