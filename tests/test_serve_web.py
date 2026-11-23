"""O web do `gaia serve`: paleta de comandos, sessoes e eco em chines.

Tres contratos que a tela depende e que quebram em silencio:

* o ``index.html`` nao pode ter ``id`` repetido nem ``$("#x")`` apontando para
  elemento que nao existe (isso foi o bug do botao "sessoes", que pegava o
  ``<dialog>`` e chamava ``showModal`` num ``<button>``);
* ``GET /commands`` serve o mesmo catalogo da ajuda do terminal;
* ``POST /chinese`` liga o eco 中文 no motor, e o ``zh`` so e emitido com ele
  ligado; ``GET /languages``/``POST /language`` sao o dialog "idiomas" (ela
  oferece a lista e o botao 中文 unico nao existe mais).
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
    for botao in ("btn-sessoes", "btn-nova", "btn-idiomas"):
        assert f'id="{botao}"' in texto
    # O atual de cada lista continua sendo o alvo do `showModal`.
    assert 'dialog id="sessoes"' in texto
    assert 'dialog id="idiomas"' in texto
    # O botao 中文 fixo saiu: quem escolhe o idioma e o dialog.
    assert 'id="btn-chines"' not in texto


def test_tela_de_automacoes_no_html():
    """Botao do header NAVEGA para a tela propria (`#automacoes`), sem dialog.

    O desenho mudou de "dialog somente leitura" para tela com checkbox: o
    contrato agora e o hash (F5/voltar do navegador), os alvos da tela e a
    leitura/escrita das mesmas rotas do motor.
    """
    texto, coletor = _html()
    assert 'id="btn-automacoes"' in texto
    assert 'id="view-automacoes"' in texto
    assert 'id="view-chat"' in texto
    assert 'dialog id="automacoes-dialog"' not in texto
    assert "automacoes" in coletor.ids
    # O botao chama a navegacao, nao `showModal`.
    assert '"#btn-automacoes").addEventListener("click", irParaAutomacoes)' in texto
    assert 'location.hash = "#automacoes"' in texto
    assert 'hashchange' in texto
    # Le o mesmo JSON do painel e nao inventa status.
    assert 'api("/automations")' in texto
    for estado in ("scheduled", "missed", "paused", "failed"):
        assert estado in texto
    # Checkbox de cada rotina e da proatividade gravam no motor.
    assert 'api("/automations/set"' in texto
    assert 'api("/proactive"' in texto
    # Status em portugues (o pedido era "se falhou ou se deu certo").
    assert '"deu certo"' in texto and '"não rodou"' in texto


def test_botao_de_voz_no_html():
    """O modo voz e um contrato entre HTML, JS e rotas `/voice/*`."""
    texto, _ = _html()
    assert 'id="btn-voz"' in texto and 'hidden' in texto.split('id="btn-voz"')[1][:200]
    # O botao so aparece depois de `GET /voice`; o Enter passa por `vozEnter`.
    assert 'carregarVoz()' in texto
    assert 'vozEnter()' in texto
    assert '/voice/listen' in texto and '/voice/speak' in texto and '/voice/stop' in texto
    # Ao terminar o turno falado, a resposta e falada e o mic volta.
    assert 'vozFalar(' in texto and 'vozSilenciar(' in texto
    # `M` e o atalho de mudo (como na TUI), nao o Enter.
    assert 'vozAlternarMudo()' in texto and 'teclaMudo(' in texto
    assert '"m"' in texto and '"M"' in texto
    # O estado "transcrevendo" aparece de verdade: a tela sonda `GET /voice`,
    # que publica a fase da escuta — antes ela so existia no rotulo.
    assert 'vozSondar(' in texto and 'dados.state === "transcrevendo"' in texto
    assert 'state' in texto


def test_modo_voz_grava_wav_no_navegador():
    """Microfone do navegador: getUserMedia -> WAV 16-bit -> /voice/upload."""
    texto, _ = _html()
    assert "navigator.mediaDevices.getUserMedia" in texto
    assert 'api("/voice/upload"' in texto
    assert '"Content-Type": "audio/wav"' in texto
    # WAV de verdade: cabecalho RIFF/WAVE e PCM 16-bit mono 16 kHz.
    assert 'texto(0, "RIFF")' in texto and 'texto(8, "WAVE")' in texto
    assert "VOZ_WAV_HZ = 16000" in texto and "new Int16Array" in texto
    # A origem do audio vem do motor (`GET /voice` -> `source`).
    assert 'dados.source === "server" ? "server" : "browser"' in texto
    assert 'voz.fonte === "browser"' in texto
    # A rota antiga continua existindo (microfone do servidor).
    assert '/voice/listen' in texto


def test_enter_solta_a_gravacao_do_navegador():
    """No modo navegador o Enter fecha a gravacao local, sem abortar o upload."""
    texto, _ = _html()
    trecho = texto.split("function vozEnter()", 1)[1].split("function ", 1)[0]
    assert 'voz.fonte === "browser"' in trecho
    assert "voz.pararGravacao()" in trecho
    assert 'vozEstado("transcrevendo")' in trecho


def test_enter_do_modo_voz_nao_silencia():
    """Enter envia; quem cala o microfone e o `M` (bug: Enter ficava mudo)."""
    texto, _ = _html()
    trecho = texto.split("function vozEnter()", 1)[1].split("function ", 1)[0]
    # No estado silenciado o Enter so avisa do atalho, nunca religa nem cala.
    assert "vozAlternarMudo(" not in trecho
    assert 'aviso(' in trecho and "M" in trecho
    # O unico caminho que pausa o microfone e o atalho/erro, nao o Enter.
    assert "voz.manual = true" in trecho


def test_paleta_de_comandos_no_html():
    texto, _ = _html()
    assert 'id="comandos"' in texto
    assert " filtrarComandos()" in texto.replace("\n", " ") or "filtrarComandos()" in texto
    # Enter/Tab/setas tratados antes do envio.
    for tecla in ("ArrowDown", "ArrowUp", "Tab", "Escape"):
        assert f'"{tecla}"' in texto


def test_icone_da_gaia_nas_duas_telas():
    """O mesmo PNG e o favicon do chat e do painel (sem arquivo duplicado)."""
    from gaia.serve import web

    assert web.ICON_PATH.is_file()
    assert "icon.png" in web.STATIC_ASSETS
    assert "icon.png" in web.CONTROL_ASSETS
    conteudo, tipo = web.icon()
    assert tipo == "image/png"
    assert conteudo.startswith(b"\x89PNG")
    for pagina in (INDEX, RAIZ / "gaia" / "serve" / "static" / "control.html"):
        texto = pagina.read_text(encoding="utf-8")
        assert 'rel="icon"' in texto
        assert "/static/icon.png" in texto


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


def test_languages_lista_o_idioma_de_eco(servidor):
    _alvo, base = servidor
    status, corpo = _pedir(base, "GET", "/languages")
    assert status == 200
    idiomas = corpo["languages"]
    assert [item["key"] for item in idiomas] == ["zh"]
    assert idiomas[0]["label"] == "中文"
    assert idiomas[0]["current"] is True
    assert idiomas[0]["enabled"] is False


def test_language_escolhe_o_idioma_e_liga_o_eco(servidor):
    """Dialog "idiomas": `{key: "zh"}` escolhe e liga; `enabled: false` desliga."""
    alvo, base = servidor
    status, corpo = _pedir(base, "POST", "/language", corpo={"key": "zh"})
    assert status == 200
    assert corpo["key"] == "zh" and corpo["chinese"] is True
    assert alvo.chinese is True
    assert corpo["languages"][0]["enabled"] is True

    status, corpo = _pedir(base, "POST", "/language", corpo={"key": "zh", "enabled": False})
    assert status == 200 and corpo["chinese"] is False
    assert alvo.chinese is False


def test_language_desconhecido_e_400(servidor):
    _, base = servidor
    status, corpo = _pedir(base, "POST", "/language", corpo={"key": "fr"})
    assert status == 400
    assert corpo["error"] == "bad_language"

    status, corpo = _pedir(base, "POST", "/language", corpo={})
    assert status == 400
    assert corpo["error"] == "empty_language"


def test_language_aceita_motor_antigo_sem_set_language(servidor):
    """Target antigo (so `set_chinese`) continua atendendo o dialog."""

    class Antigo(FakeTarget):
        set_language = None  # type: ignore[assignment]

    srv = ServeServer(Antigo(), RemoteUI(), host="127.0.0.1", port=0)  # type: ignore[arg-type]
    base = srv.start()
    try:
        status, corpo = _pedir(base, "POST", "/language", corpo={"key": "zh"})
        assert status == 200 and corpo["chinese"] is True
        status, corpo = _pedir(base, "POST", "/language", corpo={"key": "fr"})
        assert status == 400
    finally:
        srv.shutdown()


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
        # `lang` rotula a linha do eco na tela (hoje "zh").
        assert {eco["lang"] for eco in ecos} == {"zh"}
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


def test_languages_e_set_language_no_motor_de_verdade():
    orch = _Orch()
    alvo = OrchestratorTarget(orch, RemoteUI())
    idiomas = alvo.languages()
    assert [item["key"] for item in idiomas] == ["zh"]
    assert idiomas[0]["enabled"] is False
    assert alvo.set_language("zh") is True
    assert orch.cfg.interface.chinese_echo is True
    assert alvo.languages()[0]["enabled"] is True
    assert alvo.set_language("zh", False) is False
    with pytest.raises(ValueError):
        alvo.set_language("fr")


def test_set_chinese_le_a_configuracao_do_motor():
    orch = _Orch()
    alvo = OrchestratorTarget(orch, RemoteUI())
    assert alvo.chinese() is False
    assert alvo.set_chinese(True) is True
    assert orch.cfg.interface.chinese_echo is True
    assert alvo.chinese() is True
    assert alvo.set_chinese(False) is False
    assert alvo.chinese() is False
