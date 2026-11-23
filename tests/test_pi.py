"""Testes da ponte com o pi: runner (JSONL) e ferramenta `pi`.

Nada de rede e nada de pi de verdade: o subprocesso e substituido por um
callable que devolve stdout/stderr prontos, e os diretorios vivem em tmp_path.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gaia.agent.tools.base import ToolContext
from gaia.agent.tools.pi import build_pi_tools, pi_tool
from gaia.pi.config import PiConfig
from gaia.pi.runner import PiError, PiRunner

# ---------------------------------------------------------------- JSONL de exemplo


def _linha(record: dict) -> str:
    return json.dumps(record)


def _jsonl(texto: str = "ok", ferramentas: tuple[str, ...] = ()) -> str:
    """Stream minimo no formato do `pi --print --mode json`."""
    linhas = [
        _linha({"type": "session", "version": 3, "id": "sess-1", "cwd": "/tmp"}),
        _linha({"type": "agent_start"}),
        _linha({"type": "turn_start"}),
    ]
    for indice, nome in enumerate(ferramentas):
        linhas.append(
            _linha(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "toolcall_end",
                        "contentIndex": 0,
                        "toolCall": {"id": f"c{indice}", "name": nome, "arguments": {}},
                    },
                }
            )
        )
    linhas += [
        _linha(
            {
                "type": "message_update",
                "usage": {"totalTokens": 8225},
                "assistantMessageEvent": {"type": "text_delta", "delta": texto},
            }
        ),
        _linha(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": texto}],
                },
            }
        ),
        _linha({"type": "agent_settled", "aborted": False}),
    ]
    return "\n".join(linhas) + "\n"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    raiz = tmp_path / "pr-workspace"
    raiz.mkdir()
    return raiz


def _cfg(workspace: Path, **overrides) -> PiConfig:
    dados = {
        "enabled": True,
        "binary": "/usr/bin/pi",
        "workspace": str(workspace),
        "protect": [str(workspace.parent / "gaia")],
        "timeout": 30.0,
    }
    dados.update(overrides)
    return PiConfig(**dados)


def _fake_popen(monkeypatch, stdout: str = "", stderr: str = "", rc: int = 0):
    chamadas: list[list[str]] = []

    def fake(cmd, **kwargs):
        chamadas.append(list(cmd))
        return subprocess.CompletedProcess(cmd, rc, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", fake)
    return chamadas


# ---------------------------------------------------------------------- runner


def test_runner_le_jsonl_e_devolve_texto(workspace, monkeypatch):
    chamadas = _fake_popen(monkeypatch, _jsonl("pronto", ("read", "bash")))
    resultado = PiRunner(_cfg(workspace)).run("faca algo")
    assert resultado.ok
    assert resultado.text == "pronto"
    assert resultado.tool_calls == ["read", "bash"]
    assert resultado.tokens == 8225
    assert resultado.session_id == "sess-1"
    # cwd e o workspace, e o prompt vai depois de `--` (nao vira flag).
    assert chamadas[0][0] == "/usr/bin/pi"
    assert chamadas[0][-2:] == ["--", "faca algo"]
    assert "--print" in chamadas[0] and "json" in chamadas[0]


def test_runner_usa_texto_incremental_quando_falta_message_end(workspace, monkeypatch):
    linhas = [
        _linha({"type": "session", "id": "s"}),
        _linha(
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "par"},
            }
        ),
        _linha(
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "cial"},
            }
        ),
    ]
    _fake_popen(monkeypatch, "\n".join(linhas) + "\n")
    assert PiRunner(_cfg(workspace)).run("x").text == "parcial"


def test_runner_sem_texto_e_erro(workspace, monkeypatch):
    _fake_popen(monkeypatch, "", "No API key found", rc=1)
    resultado = PiRunner(_cfg(workspace)).run("x")
    assert not resultado.ok
    assert "API key" in resultado.error


def test_runner_recusa_workspace_fora_da_raiz(workspace, tmp_path):
    fora = tmp_path / "outro"
    fora.mkdir()
    with pytest.raises(PiError, match="fora de"):
        PiRunner(_cfg(workspace)).run("x", fora)


def test_runner_recusa_diretorio_protegido(workspace, tmp_path, monkeypatch):
    protegido = tmp_path / "gaia"
    protegido.mkdir()
    _fake_popen(monkeypatch, _jsonl())
    cfg = _cfg(workspace, protect=[str(protegido)])
    runner = PiRunner(cfg)
    with pytest.raises(PiError, match="protect"):
        runner.run("x", protegido)


def test_runner_recusa_workspace_inexistente(tmp_path, monkeypatch):
    raiz = tmp_path / "pr-workspace"
    raiz.mkdir()
    _fake_popen(monkeypatch, _jsonl())
    with pytest.raises(PiError, match="nao existe"):
        PiRunner(_cfg(raiz)).run("x", raiz / "nao-existe")


def test_runner_prompt_vazio(workspace):
    with pytest.raises(PiError, match="prompt vazio"):
        PiRunner(_cfg(workspace)).run("   ")


def test_runner_binario_ausente(tmp_path):
    cfg = PiConfig(binary="/nao/existe/pi", workspace=str(tmp_path))
    with pytest.raises(PiError, match=r"nao encontrado|nao foi possivel"):
        PiRunner(cfg).run("x", tmp_path)


def test_runner_timeout_vira_erro_claro(workspace, monkeypatch):
    def estoura(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr(subprocess, "run", estoura)
    with pytest.raises(PiError, match="excedeu"):
        PiRunner(_cfg(workspace, timeout=5)).run("x")


def test_runner_aceita_workspace_relativo_do_config(workspace, monkeypatch):
    sub = workspace / "sub"
    sub.mkdir()
    chamadas = _fake_popen(monkeypatch, _jsonl("ok"))
    PiRunner(_cfg(workspace)).run("x", sub)
    assert Path(chamadas[0][0]).name == "pi"


# -------------------------------------------------------------------- ferramenta


def _ctx(workspace: Path, cfg: PiConfig | None = None) -> ToolContext:
    return ToolContext(Path(".").resolve(), pi=cfg or _cfg(workspace))


def test_ferramenta_nao_existe_quando_desligada(workspace):
    ctx = ToolContext(Path(".").resolve(), pi=PiConfig(enabled=False))
    from gaia.agent.tools import build_tools

    assert "pi" not in build_tools(ctx).tools


def test_ferramenta_aparece_quando_ligada(workspace):
    from gaia.agent.tools import build_tools

    tools = build_tools(_ctx(workspace))
    assert "pi" in tools.tools
    assert tools.get("pi").requires_approval is True


def test_ferramenta_exige_prompt(workspace):
    resultado = pi_tool({}, _ctx(workspace))
    assert not resultado.ok
    assert "vazio" in resultado.content


def test_ferramenta_devolve_texto_e_resumo(workspace, monkeypatch):
    _fake_popen(monkeypatch, _jsonl("abri o PR #12", ("bash", "edit")))
    resultado = pi_tool({"prompt": "abre um PR"}, _ctx(workspace))
    assert resultado.ok
    assert "abri o PR #12" in resultado.content
    assert "[pi]" in resultado.content
    assert "bash, edit" in resultado.content


def test_ferramenta_trunca_texto_longo(workspace, monkeypatch):
    _fake_popen(monkeypatch, _jsonl("x" * 5000))
    cfg = _cfg(workspace, max_output_chars=500)
    resultado = pi_tool({"prompt": "x"}, _ctx(workspace, cfg))
    assert resultado.ok
    assert "truncado" in resultado.content
    assert len(resultado.content) < 1000


def test_ferramenta_falha_devolve_motivo(workspace, monkeypatch):
    _fake_popen(monkeypatch, "", "gh: not authenticated", rc=1)
    resultado = pi_tool({"prompt": "abre PR"}, _ctx(workspace))
    assert not resultado.ok
    assert "not authenticated" in resultado.content


def test_ferramenta_reporta_pi_desligado(workspace):
    cfg = _cfg(workspace, enabled=False)
    assert not pi_tool({"prompt": "x"}, _ctx(workspace, cfg)).ok


def test_build_pi_tools_um_so(workspace):
    ferramentas = build_pi_tools()
    assert [f.name for f in ferramentas] == ["pi"]
