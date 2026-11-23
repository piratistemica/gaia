"""O web do `gaia serve`: paleta de comandos, sessoes e eco em chines.

Tres contratos que a tela depende e que quebram em silencio:

* o ``index.html`` nao pode ter ``id`` repetido nem ``$("#x")`` apontando para
  elemento que nao existe (isso foi o bug do botao "sessoes", que pegava o
  ``<dialog>`` e chamava ``showModal`` num ``<button>``);
* ``GET /commands`` serve o mesmo catalogo da ajuda do terminal;
* ``POST /chinese`` liga o eco 中文 no motor, e o ``zh`` so e emitido com ele
  ligado.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from gaia.controller import command_catalog
from gaia.interface.remote import RemoteUI
from gaia.serve.app import OrchestratorTarget
from gaia.serve.server import ServeServer

RAIZ = Path(__file__).resolve().parent.parent
INDEX = RAIZ / "gaia" / "serve" / "static" / "index.html"


# ------------------------------------------------------------------ html ---


class _Ids(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.soma: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for nome, valor in attrs:
            if nome == "id" and valor:
                self.ids.append(valor)


def _html() -> tuple[str, _Ids]:
    texto = INDEX.read_text(encoding="utf-8")
    coletor = _Ids()
    coletor.feed(texto)
    return texto, coletor


def test_nenhum_id_repetido_no_index():
    """`#sessoes` era o botao *e* o dialog: `$("#sessoes")` devolvia o botao."""
    _, coletor = _html()
    repetidos = {item for item in coletor.ids if coletor.ids.count(item) > 1}
    assert repetidos == set()


def test_todo_seletor_de_id_existe_no_html():
    texto, coletor = _html()
    usados = set(re.findall(r'\$\("#([a-z0-9-]+)"\)', texto))
    faltando = usados - set(coletor.ids)
    assert faltando == set(), f"$() aponta para id inexistente: {faltando}"


def test_botoes_novos_do_header():
    texto, _ = _html()
    for botao in ("btn-sessoes", "btn-nova", "btn-chines"):
        assert f'id="{botao}"' in texto
    # O dialog continua sendo o alvo do `showModal`.
    assert 'dialog id="sessoes"' in texto


def test_paleta_de_comandos_no_html():
    texto, _ = _html()
    assert 'id="comandos"' in texto
    assert " filtrarComandos()" in texto.replace("\n", " ") or "filtrarComandos()" in texto
    # Enter/Tab/setas tratados antes do envio.
    for tecla in ("ArrowDown", "ArrowUp", "Tab", "Escape"):
        assert f'"{tecla}"' in texto


# -------------------------------------------------------------- catalogo ---


class FakeTarget:
    """Target minimo para as rotas novas (catalogo e chines)."""

    def __init__(self) -> None:
        self.chinese = False

    def health(self) -> dict[str, Any]:
        return {"model": "deepseek/flash", "session": "s1", "chinese": self.chinese}

    def sessions(self) -> list[dict[str, Any]]:
        return []

    def models(self) -> list[dict[str, Any]]:
        return []

    def switch_model(self, slug: str) -> list[str]:
        return []

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        return []

    def turn(self, text: str) -> str:
        return "r_1"

    def decide(self, request_id: str, decision: str) -> None:
        return None

    def abort(self) -> None:
        return None

    def set_chinese(self, enabled: bool) -> bool:
        self.chinese = bool(enabled)
        return self.chinese


def _pedir(
    base: str, metodo: str, caminho: str, *, corpo: dict[str, Any] | None = None
) -> tuple[int, Any]:
    host, porta = base.removeprefix("http://").split(":")
    conexao = http.client.HTTPConnection(host, int(porta), timeout=10)
    dados = json.dumps(corpo).encode() if corpo is not None else None
    cabecalhos = {"Content-Type": "application/json"} if dados else {}
    conexao.request(metodo, caminho, body=dados, headers=cabecalhos)
    resposta = conexao.getresponse()
    bruto = resposta.read()
    conexao.close()
    try:
        return resposta.status, json.loads(bruto)
    except ValueError:
        return resposta.status, bruto


@pytest.fixture()
def servidor():
    alvo = FakeTarget()
    srv = ServeServer(alvo, RemoteUI(), host="127.0.0.1", port=0)  # type: ignore[arg-type]
    base = srv.start()
    try:
        yield alvo, base
    finally:
        srv.shutdown()


def test_commands_serve_o_catalogo(servidor):
    _, base = servidor
    status, corpo = _pedir(base, "GET", "/commands")
    assert status == 200
    comandos = corpo["commands"]
    assert [item["usage"] for item in comandos] == [
        item["usage"] for item in command_catalog.as_dicts()
    ]
    ajuda = next(item for item in comandos if item["usage"] == "/ajuda")
    assert ajuda["names"] == ["/ajuda", "/help"]
    assert ajuda["argument"] is False


def test_chinese_liga_e_desliga_no_motor(servidor):
    alvo, base = servidor
    status, corpo = _pedir(base, "POST", "/chinese", corpo={"enabled": True})
    assert status == 200
    assert corpo == {"ok": True, "chinese": True}
    assert alvo.chinese is True

    status, corpo = _pedir(base, "GET", "/engine")
    assert status == 200
    assert corpo["chinese"] is True

    status, corpo = _pedir(base, "POST", "/chinese", corpo={"enabled": False})
    assert status == 200 and corpo["chinese"] is False
    assert alvo.chinese is False


def test_chinese_sem_suporte_no_motor_e_400():
    class SemChines(FakeTarget):
        set_chinese = None  # type: ignore[assignment]

    srv = ServeServer(SemChines(), RemoteUI(), host="127.0.0.1", port=0)  # type: ignore[arg-type]
    base = srv.start()
    try:
        status, corpo = _pedir(base, "POST", "/chinese", corpo={"enabled": True})
        assert status == 400
        assert corpo["error"] == "chinese_failed"
    finally:
        srv.shutdown()


# ------------------------------------------------------- motor de verdade ---


class _Cfg:
    def __init__(self) -> None:
        self.interface = type("I", (), {"chinese_echo": False})()
        self.agent = type("A", (), {"model_slug": lambda self: "deepseek/flash"})()


class _Orch:
    """Orchestrator falso so com o que o eco 中文 precisa."""

    def __init__(self, traducao: str = "你好 | nǐ hǎo") -> None:
        self.cfg = _Cfg()
        self.session_id = "s1"
        self.traducao = traducao
        self.chamadas: list[str] = []
        self.mode_name = "assistente"

    async def translate_zh(self, text: str) -> str:
        self.chamadas.append(text)
        return self.traducao

    async def answer_once(self, text: str, **_: Any) -> str:
        return f"eco: {text}"


def _loop_em_thread() -> tuple[asyncio.AbstractEventLoop, Any]:
    import threading

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop, thread


def _drenar(ui: RemoteUI, limite: float = 3.0) -> list[dict[str, Any]]:
    """Todos os eventos emitidos em `limite` segundos (nada se perde)."""
    import time

    achados: list[dict[str, Any]] = []
    fim = time.time() + limite
    while time.time() < fim:
        while not ui.events.empty():
            achados.append(ui.events.get_nowait())
    return achados


def _de(eventos: list[dict[str, Any]], tipo: str) -> list[dict[str, Any]]:
    return [evento for evento in eventos if evento["type"] == tipo]


def test_eco_zh_so_com_o_interruptor_ligado():
    loop, thread = _loop_em_thread()
    try:
        orch = _Orch()
        ui = RemoteUI()
        alvo = OrchestratorTarget(orch, ui)
        alvo.attach_loop(loop)

        alvo.turn("bom dia")
        antes = _drenar(ui, limite=1.5)
        assert _de(antes, "run_finished")
        assert _de(antes, "zh") == []
        assert orch.chamadas == []

        alvo.set_chinese(True)
        alvo.turn("bom dia")
        depois = _drenar(ui, limite=2.0)
        ecos = _de(depois, "zh")
        assert [eco["role"] for eco in ecos] == ["user", "gaia"]
        assert ecos[0]["zh"] == "你好 | nǐ hǎo"
        # A pergunta e a resposta passaram pelo tradutor.
        assert orch.chamadas == ["bom dia", "eco: bom dia"]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3)


def test_eco_zh_nao_derruba_o_turno_quando_a_traducao_falha():
    loop, thread = _loop_em_thread()

    class Quebrado(_Orch):
        async def translate_zh(self, text: str) -> str:
            raise RuntimeError("sem rede")

    try:
        ui = RemoteUI()
        alvo = OrchestratorTarget(Quebrado(), ui)
        alvo.attach_loop(loop)
        alvo.set_chinese(True)
        alvo.turn("oi")
        eventos = _drenar(ui, limite=2.0)
        fim = _de(eventos, "run_finished")
        assert fim and fim[0]["ok"] is True
        assert _de(eventos, "zh") == []
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3)


def test_set_chinese_le_a_configuracao_do_motor():
    orch = _Orch()
    alvo = OrchestratorTarget(orch, RemoteUI())
    assert alvo.chinese() is False
    assert alvo.set_chinese(True) is True
    assert orch.cfg.interface.chinese_echo is True
    assert alvo.chinese() is True
    assert alvo.set_chinese(False) is False
    assert alvo.chinese() is False
