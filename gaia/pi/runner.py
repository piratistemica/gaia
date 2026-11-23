"""Executa o pi em modo de uma pergunta e devolve o resultado.

`pi --print --mode json` escreve um JSONL em stdout: um header de sessao, os
eventos do turno e `agent_settled` no fim. Aqui o stream e lido e reduzido a
um `PiResult` (texto final, ferramentas usadas, tokens). Nada de rede: quem
fala com o provedor e o proprio pi.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gaia.pi.config import PiConfig

# Mesmo motivo do teto do bash: saida realimentada no contexto do modelo.
OUTPUT_LIMIT = 20000


class PiError(RuntimeError):
    pass


@dataclass
class PiResult:
    ok: bool
    text: str
    tool_calls: list[str] = field(default_factory=list)
    tokens: int = 0
    duration_s: float = 0.0
    session_id: str = ""
    error: str = ""

    def resume(self) -> str:
        """Resumo curto para o log/depuracao (sem despejar o texto inteiro)."""
        marcas = f"{len(self.tool_calls)} ferramenta(s), {self.tokens} tokens"
        if not self.ok:
            return f"falhou: {self.error or self.text} ({marcas})"
        return f"ok em {self.duration_s:.0f}s ({marcas})"


def _resolve_binary(cfg: PiConfig) -> str:
    if cfg.binary.strip():
        return str(Path(cfg.binary).expanduser())
    encontrado = shutil.which("pi")
    if encontrado:
        return encontrado
    # O instalador gerenciado deixa o binario aqui e nem sempre no PATH do
    # servico (unidade de usuario sem login shell).
    caseiro = Path.home() / ".pi" / "agent" / "bin" / "pi"
    if caseiro.is_file():
        return str(caseiro)
    raise PiError(
        "pi nao encontrado: instale com `curl -fsSL https://pi.dev/install.sh | sh` "
        "ou aponte [pi] binary no config.toml"
    )


def _evento_texto(record: dict[str, Any]) -> str:
    """Texto incremental de um `message_update`."""
    interno = record.get("assistantMessageEvent") or {}
    if interno.get("type") == "text_delta":
        return str(interno.get("delta") or "")
    return ""


def _evento_tool(record: dict[str, Any]) -> str:
    interno = record.get("assistantMessageEvent") or {}
    if interno.get("type") != "toolcall_end":
        return ""
    chamada = interno.get("toolCall") or {}
    return str(chamada.get("name") or "")


def _texto_final(record: dict[str, Any]) -> str:
    """Texto autoritativo de um `message_end` do assistente."""
    mensagem = record.get("message") or {}
    if mensagem.get("role") != "assistant":
        return ""
    partes = [
        str(bloco.get("text") or "")
        for bloco in mensagem.get("content") or []
        if isinstance(bloco, dict) and bloco.get("type") == "text"
    ]
    return "".join(partes)


def _tokens(record: dict[str, Any]) -> int:
    uso = record.get("usage") or {}
    try:
        return int(uso.get("totalTokens") or 0)
    except (TypeError, ValueError):
        return 0


@dataclass
class _Acumulado:
    texto: str = ""
    final: str = ""
    tools: list[str] = field(default_factory=list)
    tokens: int = 0
    session_id: str = ""
    erro: str = ""

    def absorver(self, record: dict[str, Any]) -> None:
        tipo = record.get("type")
        if tipo == "session":
            self.session_id = str(record.get("id") or self.session_id)
            return
        if tipo in ("message_update", "turn_end", "agent_end"):
            self.tokens = max(self.tokens, _tokens(record))
        if tipo == "message_update":
            self.texto += _evento_texto(record)
            nome = _evento_tool(record)
            if nome:
                self.tools.append(nome)
            return
        if tipo == "message_end":
            final = _texto_final(record)
            if final:
                self.final = final
            return
        if tipo in ("error", "agent_settled") and record.get("error"):
            self.erro = str(record.get("error"))

    @property
    def resposta(self) -> str:
        return (self.final or self.texto).strip()


class PiRunner:
    """Um processo do pi por chamada (o modelo nao guarda estado entre turnos)."""

    def __init__(self, cfg: PiConfig, env: dict[str, str] | None = None):
        self.cfg = cfg
        self._env = env

    # ------------------------------------------------------------------ helpers
    def _ambiente(self) -> dict[str, str]:
        base = dict(os.environ)
        if self._env:
            base.update(self._env)
        caminho = str(Path.home() / ".pi" / "agent" / "bin")
        atual = base.get("PATH", "")
        if caminho not in atual.split(os.pathsep):
            base["PATH"] = f"{caminho}{os.pathsep}{atual}" if atual else caminho
        # Sem terminal: o pi nao pode abrir a TUI nem pedir confirmacao.
        base.setdefault("CI", "1")
        return base

    def _destino(self, workspace: Path | None) -> Path:
        raiz = self.cfg.root()
        pedido = (workspace or raiz).expanduser()
        try:
            pedido = pedido.resolve()
        except OSError as exc:
            raise PiError(f"workspace invalido: {pedido}") from exc
        if pedido != raiz and raiz not in pedido.parents:
            for proibido in self.cfg.protected():
                if pedido == proibido or proibido in pedido.parents:
                    raise PiError(
                        f"{pedido} esta na lista pi.protect: o deploy reseta esse clone"
                    )
            raise PiError(
                f"workspace {pedido} fora de {raiz} (pi.workspace no config.toml)"
            )
        for proibido in self.cfg.protected():
            if pedido == proibido or proibido in pedido.parents:
                raise PiError(
                    f"{pedido} esta na lista pi.protect: o deploy reseta esse clone"
                )
        if not pedido.is_dir():
            raise PiError(f"workspace nao existe: {pedido}")
        return pedido

    def _argumentos(self, prompt: str) -> list[str]:
        args = ["--print", "--mode", "json"]
        if self.cfg.model.strip():
            args += ["--model", self.cfg.model.strip()]
        args += list(self.cfg.extra_args)
        # `--` encerra as flags: sem isso um prompt comecando com "-" viraria
        # opcao do pi (o texto vem do modelo, entao e conteudo nao confiavel).
        args += ["--", prompt]
        return args

    # ------------------------------------------------------------------ execucao
    def run(self, prompt: str, workspace: Path | None = None) -> PiResult:
        pedido = (prompt or "").strip()
        if not pedido:
            raise PiError("prompt vazio")
        destino = self._destino(workspace)
        binario = _resolve_binary(self.cfg)
        inicio = time.monotonic()
        try:
            proc = subprocess.run(
                [binario, *self._argumentos(pedido)],
                cwd=str(destino),
                capture_output=True,
                text=True,
                timeout=self.cfg.timeout,
                env=self._ambiente(),
            )
        except subprocess.TimeoutExpired:
            raise PiError(f"pi excedeu {self.cfg.timeout:.0f}s e foi encerrado") from None
        except OSError as exc:
            raise PiError(f"nao foi possivel executar o pi: {exc}") from exc

        acumulado = _Acumulado()
        for linha in proc.stdout.splitlines():
            linha = linha.strip()
            if not linha:
                continue
            try:
                record = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                acumulado.absorver(record)

        duracao = time.monotonic() - inicio
        stderr = (proc.stderr or "").strip()
        texto = acumulado.resposta
        if proc.returncode != 0 and not texto and not acumulado.erro:
            acumulado.erro = stderr[-1000:] or f"exit {proc.returncode}"
        return PiResult(
            ok=proc.returncode == 0 and bool(texto),
            text=texto,
            tool_calls=acumulado.tools,
            tokens=acumulado.tokens,
            duration_s=duracao,
            session_id=acumulado.session_id,
            error=acumulado.erro or (stderr[-1000:] if proc.returncode != 0 else ""),
        )

    async def run_async(self, prompt: str, workspace: Path | None = None) -> PiResult:
        import asyncio

        return await asyncio.to_thread(self.run, prompt, workspace)


__all__ = ["OUTPUT_LIMIT", "PiError", "PiResult", "PiRunner"]
