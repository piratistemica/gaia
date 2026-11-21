"""Cliente remoto contra o servidor real: nada de mock de HTTP.

O `ServeServer` sobe na porta 0 com um `Target` falso, e o `RemoteClient` fala
com ele de verdade — framing SSE, autorização, reconexão e aprovação inclusos.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from gaia.client.remote_client import RemoteClient, RemoteError
from gaia.interface.remote import RemoteUI
from gaia.serve.server import ServeServer


class FakeTarget:
    def __init__(self) -> None:
        self.turnos: list[str] = []
        self.decisoes: list[tuple[str, str]] = []
        self.abortos = 0

    def health(self) -> dict[str, Any]:
        return {"model": "deepseek/flash", "session": "s1", "busy": False}

    def sessions(self) -> list[dict[str, Any]]:
        return [{"id": 1, "channel": "serve"}]

    def turn(self, text: str) -> str:
        self.turnos.append(text)
        return "r_1"

    def decide(self, request_id: str, decision: str) -> None:
        self.decisoes.append((request_id, decision))

    def abort(self) -> None:
        self.abortos += 1


class UIFalsa:
    """Registra as chamadas que o cliente faria no TerminalUI."""

    def __init__(self, *, decisao: str = "once") -> None:
        self.chamadas: list[tuple[str, Any]] = []
        self.decisao = decisao
        self.fechado = False

    def __getattr__(self, nome: str):
        def registrar(*args: Any, **kwargs: Any) -> Any:
            self.chamadas.append((nome, args))
            return None

        return registrar

    def permission(self, ferramenta: str, recursos: list[str]) -> str:
        self.chamadas.append(("permission", (ferramenta, recursos)))
        return self.decisao

    def close(self) -> None:
        self.fechado = True


@pytest.fixture()
def par():
    ui = RemoteUI()
    alvo = FakeTarget()
    srv = ServeServer(alvo, ui, host="127.0.0.1", port=0)
    url = srv.start()
    try:
        yield alvo, ui, url
    finally:
        srv.shutdown()


def _tipos(ui: UIFalsa) -> list[str]:
    return [nome for nome, _ in ui.chamadas]


def _conectar(ui: UIFalsa, cliente: RemoteClient, servidor_ui) -> threading.Thread:
    """Abre o stream e só devolve quando o cliente estiver realmente inscrito."""
    thread = threading.Thread(target=cliente.stream, daemon=True)
    thread.start()
    for _ in range(150):
        servidor_ui.info("handshake")
        time.sleep(0.02)
        if "info" in _tipos(ui):
            return thread
    raise AssertionError("o cliente nao se inscreveu no stream")


def test_health_e_sessions_pelo_cliente(par):
    _, _, url = par
    cliente = RemoteClient(url, ui=UIFalsa())
    assert cliente.health()["model"] == "deepseek/flash"
    assert cliente.sessions()[0]["channel"] == "serve"


def test_turn_chega_no_servidor(par):
    alvo, _, url = par
    cliente = RemoteClient(url, ui=UIFalsa())
    assert cliente.turn("oi") == "r_1"
    assert alvo.turnos == ["oi"]


def test_token_errado_levanta_remote_error(par):
    _, _, _url = par
    servidor = ServeServer(FakeTarget(), RemoteUI(), host="127.0.0.1", port=0, token="s")
    outra = servidor.start()
    try:
        # /health e publico de proposito (monitoracao); o resto exige token.
        with pytest.raises(RemoteError, match="token"):
            RemoteClient(outra, token="errado", ui=UIFalsa()).sessions()
        assert RemoteClient(outra, token="s", ui=UIFalsa()).sessions()[0]["id"] == 1
    finally:
        servidor.shutdown()


def test_servidor_fora_do_ar_levanta_remote_error():
    with pytest.raises(RemoteError, match="nao consegui falar"):
        RemoteClient("http://127.0.0.1:1", ui=UIFalsa()).health()


def test_cliente_desenha_eventos_do_stream(par):
    _, ui_servidor, url = par
    ui = UIFalsa()
    cliente = RemoteClient(url, ui=ui)
    thread = _conectar(ui, cliente, ui_servidor)

    ui_servidor.info("bom dia")
    ui_servidor.print_markdown("# titulo")
    ui_servidor.tool("bash", "ls")
    ui_servidor.begin_stream()
    ui_servidor.update_stream("chunk")
    ui_servidor.end_stream()

    limite = time.time() + 3
    while time.time() < limite and "end_stream" not in _tipos(ui):
        time.sleep(0.02)
    cliente._parar.set()
    thread.join(timeout=3)

    assert "info" in _tipos(ui)
    assert any(n == "print_markdown" for n in _tipos(ui))
    assert any(n == "tool" and a == ("bash", "ls") for n, a in ui.chamadas)
    assert "begin_stream" in _tipos(ui)
    assert "end_stream" in _tipos(ui)


def test_aprovacao_remota_volta_para_o_servidor(par):
    alvo, ui_servidor, url = par
    ui = UIFalsa(decisao="always")
    cliente = RemoteClient(url, ui=ui)
    thread = _conectar(ui, cliente, ui_servidor)

    # o servidor pede permissao (como se o turno estivesse esperando nela)
    pedido: dict[str, str] = {}

    def esperar() -> None:
        pedido["r"] = ui_servidor.permission("write", ["a.py"], request_id="p1")

    espera = threading.Thread(target=esperar, daemon=True)
    espera.start()
    espera.join(timeout=10)
    cliente._parar.set()
    thread.join(timeout=3)

    assert pedido.get("r") == "always"
    assert alvo.decisoes == [("p1", "always")]
    assert ui_servidor.pendentes() == []


def test_ask_remoto_espera_o_run_finished(par):
    _, ui_servidor, url = par
    ui = UIFalsa()
    cliente = RemoteClient(url, ui=ui)

    def emitir() -> None:
        for _ in range(150):
            ui_servidor.emit("run_finished", ok=True, text="pronto", run="r_1")
            time.sleep(0.02)

    threading.Thread(target=emitir, daemon=True).start()
    assert cliente.pedir_um_turno("oi") == "pronto"


def test_render_nao_quebra_com_evento_desconhecido():
    ui = UIFalsa()
    assert RemoteClient("http://127.0.0.1:1", ui=ui).render({"seq": 1, "type": "x"}) is True


def test_render_de_closed_encerra():
    cliente = RemoteClient("http://127.0.0.1:1", ui=UIFalsa())
    assert cliente.render({"seq": 1, "type": "closed"}) is False


def test_render_de_falha_mostra_erro():
    ui = UIFalsa()
    RemoteClient("http://127.0.0.1:1", ui=ui).render(
        {"seq": 1, "type": "run_finished", "ok": False, "error": "caiu"}
    )
    assert "error" in _tipos(ui)


def test_render_de_status_none_limpa():
    ui = UIFalsa()
    cliente = RemoteClient("http://127.0.0.1:1", ui=ui)
    cliente.render({"seq": 1, "type": "status", "text": None})
    cliente.render({"seq": 2, "type": "status", "text": "pensando"})
    assert [n for n in _tipos(ui) if n in {"clear_status", "set_status"}] == [
        "clear_status",
        "set_status",
    ]


def test_ultimo_seq_acompanha_os_eventos(par):
    _, _, url = par
    cliente = RemoteClient(url, ui=UIFalsa())
    cliente.render({"seq": 7, "type": "notice", "level": "info", "text": "x"})
    assert cliente.ultimo_seq == 7
