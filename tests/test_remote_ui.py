"""Contrato do `RemoteUI`: o que a interface serializa e o que nunca bloqueia."""

from __future__ import annotations

import json
import re
from pathlib import Path

from zaya.interface.remote import RENDER_METHODS, STREAM_METHODS, RemoteUI

RAIZ = Path(__file__).resolve().parent.parent


def _eventos(ui: RemoteUI) -> list[dict]:
    out = []
    while not ui.events.empty():
        out.append(ui.events.get_nowait())
    return out


def test_todo_metodo_do_contrato_existe():
    faltando = [name for name in RENDER_METHODS if not hasattr(RemoteUI, name)]
    assert faltando == []


def test_avisos_agrupam_os_cinco_niveis():
    ui = RemoteUI()
    ui.hint("dica")
    ui.info("info")
    ui.success("ok")
    ui.warning("cuidado")
    ui.error("erro")
    eventos = _eventos(ui)
    assert [(e["type"], e["level"]) for e in eventos] == [
        ("notice", "hint"),
        ("notice", "info"),
        ("notice", "success"),
        ("notice", "warning"),
        ("notice", "error"),
    ]


def test_seq_cresce_e_comeca_em_um():
    ui = RemoteUI()
    ui.info("a")
    ui.info("b")
    eventos = _eventos(ui)
    assert [e["seq"] for e in eventos] == [1, 2]
    assert [e["v"] for e in eventos] == [1, 1]


def test_todo_evento_e_json_serializavel():
    ui = RemoteUI(run="r_1")
    ui.status(Path("/tmp/x"), "sess", "deepseek/flash", 12, "edge")
    ui.banner(Path("/tmp/x"), "sess", "deepseek/flash", 12, "edge", True)
    ui.tool("bash", "ls -la")
    ui.write_diff("a.py", "-x\n+y")
    ui.check(True, "agenda", "3 tarefas")
    ui.info("oi")
    for evento in _eventos(ui):
        json.dumps(evento)  # nao pode estourar


def test_status_converte_path_para_texto():
    ui = RemoteUI()
    ui.status(Path("/home/talita"), "s1", "m", 0, "none")
    evento = _eventos(ui)[0]
    assert evento["type"] == "session"
    assert evento["project"] == "/home/talita"


def test_permission_nao_bloqueia_e_nao_aprova():
    ui = RemoteUI()
    assert ui.permission("bash", ["rm -rf /"]) == "reject"
    evento = _eventos(ui)[0]
    assert evento["type"] == "permission_ignored"


def test_prompt_devolve_vazio_sem_esperar_entrada():
    ui = RemoteUI()
    assert ui.prompt("Você") == ""
    assert _eventos(ui)[0]["type"] == "prompt"


def test_stream_marca_inicio_e_fim():
    ui = RemoteUI()
    ui.begin_stream()
    ui.update_stream("parcial")
    ui.finish_stream("final")
    tipos = [e["type"] for e in _eventos(ui)]
    assert tipos == ["stream_begin", "stream_delta", "stream_end"]


def test_write_diff_vazio_nao_polui_o_stream():
    ui = RemoteUI()
    ui.write_diff("a.py", "   ")
    assert _eventos(ui) == []


def test_nao_escreve_nada_no_stdout(capsys):
    ui = RemoteUI()
    ui.info("oi")
    ui.print_markdown("# titulo")
    ui.tool("read", "a.py")
    ui.clear_status()
    assert capsys.readouterr().out == ""


def test_console_shim_vira_say():
    ui = RemoteUI()
    ui.console.print("linha do historico")
    assert _eventos(ui)[0]["type"] == "say"


def test_controller_so_usa_metodos_do_contrato():
    """Guarda de deriva: `ui.<novo>(` no controller obriga atualizar o remote."""
    chamadas: set[str] = set()
    padrao = re.compile(r"\bui\.([a-z_][a-z0-9_]*)\(")
    for arquivo in (RAIZ / "zaya").rglob("*.py"):
        if "interface" in arquivo.parts:
            continue
        chamadas.update(padrao.findall(arquivo.read_text(encoding="utf-8")))
    fora = sorted(chamadas - set(RENDER_METHODS) - set(STREAM_METHODS))
    assert fora == [], f"ui.{fora} usado no codigo sem par no RemoteUI"


def test_streaming_de_dois_turnos_nao_vaza_estado():
    ui = RemoteUI()
    ui.begin_stream()
    ui.finish_stream("a")
    ui.begin_stream()
    ui.finish_stream("b")
    tipos = [e["type"] for e in _eventos(ui)]
    assert tipos == ["stream_begin", "stream_end", "stream_begin", "stream_end"]


def test_dois_assinantes_recebem_o_mesmo_evento():
    ui = RemoteUI()
    a = ui.subscribe()
    b = ui.subscribe()
    ui.info("oi")
    assert a.get_nowait()["text"] == "oi"
    assert b.get_nowait()["text"] == "oi"


def test_since_entrega_so_o_que_faltou():
    ui = RemoteUI()
    ui.info("antigo")
    ultimo = _eventos(ui)[-1]["seq"]
    ui.info("novo")
    fila = ui.subscribe(since=ultimo)
    assert fila.get_nowait()["text"] == "novo"
    assert fila.empty()


def test_unsubscribe_para_de_receber():
    ui = RemoteUI()
    fila = ui.subscribe()
    ui.unsubscribe(fila)
    ui.info("depois")
    assert fila.empty()


def test_historico_nao_estoura():
    from zaya.interface.remote import HISTORY

    ui = RemoteUI()
    for _ in range(HISTORY + 5):
        ui.info("x")
    assert len(ui.history) == HISTORY
