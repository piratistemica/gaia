"""Ferramenta ``pi``: delega uma tarefa de codigo ao harness pi.

O pi trabalha num clone proprio (`pi.workspace`, por padrao `~/pr-workspace`),
nunca no diretorio do agente: no mini, `~/gaia` e resetado a cada 5 min pelo
deploy. Devolve o texto final do pi mais o resumo das ferramentas que ele usou.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gaia.agent.errors import ToolError
from gaia.agent.tools.base import Tool, ToolContext, ToolResult, schema, string
from gaia.pi.runner import PiError, PiResult, PiRunner

if TYPE_CHECKING:
    from gaia.pi.config import PiConfig


def _cfg(ctx: ToolContext) -> PiConfig:
    cfg = getattr(ctx, "pi", None)
    if cfg is None:
        raise ToolError("pi nao configurado")
    return cfg


def _formatar(resultado: PiResult, limite: int) -> tuple[str, bool]:
    texto = resultado.text or resultado.error or "(sem texto)"
    if len(texto) > limite:
        corte = texto[:limite]
        return f"{corte}\n\n[texto truncado em {limite} caracteres]", True
    return texto, False


def pi_tool(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    cfg = _cfg(ctx)
    if not cfg.enabled:
        return ToolResult.failure("pi desligado (pi.enabled=false)")
    prompt = str(arguments.get("prompt") or "").strip()
    if not prompt:
        return ToolResult.failure("prompt vazio")
    workspace = str(arguments.get("workspace") or "").strip()
    # Subpasta do workspace do pi: quem valida a raiz e o runner, nao o
    # `ctx.resolve` (o clone do pi fica fora do diretorio do agente).
    destino = (cfg.root() / workspace) if workspace else None
    runner = PiRunner(cfg)
    try:
        resultado = runner.run(prompt, destino)
    except PiError as exc:
        return ToolResult.failure(str(exc))
    corpo, truncado = _formatar(resultado, max(cfg.max_output_chars, 500))
    if not resultado.ok:
        return ToolResult.failure(corpo)
    marcas = []
    if resultado.tool_calls:
        marcas.append("ferramentas: " + ", ".join(resultado.tool_calls))
    if resultado.tokens:
        marcas.append(f"{resultado.tokens} tokens")
    if resultado.duration_s:
        marcas.append(f"{resultado.duration_s:.0f}s")
    if truncado:
        marcas.append("truncado")
    sufixo = f"\n\n[pi] {' | '.join(marcas)}" if marcas else ""
    return ToolResult.success(corpo + sufixo)


PI_TOOLS: tuple[dict[str, Any], ...] = (
    dict(
        name="pi",
        description=(
            "Delega uma tarefa de codigo ao harness pi num clone separado "
            "(pi.workspace). Use para trabalhar em repositorio: ler, editar, "
            "rodar testes e abrir PR com `gh`. Devolve o texto final do pi."
        ),
        parameters=schema(
            {
                "prompt": string("Tarefa em linguagem natural, com o resultado esperado."),
                "workspace": string("Subpasta do workspace (opcional)."),
            },
            ["prompt"],
        ),
        handler=pi_tool,
        requires_approval=True,
    ),
)


def build_pi_tools() -> tuple[Tool, ...]:
    return tuple(Tool(**spec) for spec in PI_TOOLS)


__all__ = ["PI_TOOLS", "build_pi_tools", "pi_tool"]
