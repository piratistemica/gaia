"""Configuracao da ponte com o pi ([pi] do config.toml)."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class PiConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Desligado por padrao: a ferramenta so existe quando alguem liga.
    enabled: bool = False
    # Binario do pi. Vazio => "pi" (o instalador o deixa em ~/.pi/agent/bin).
    binary: str = ""
    # Unica raiz onde o pi pode trabalhar. Fora dela a ferramenta recusa.
    workspace: str = "~/pr-workspace"
    # Diretorios proibidos mesmo dentro da raiz (o clone que o deploy reseta).
    protect: list[str] = ["~/gaia"]
    # Modelo/provedor repassado como --model (vazio = o do settings do pi).
    model: str = ""
    # Tempo maximo de uma execucao (segundos). O pi tem turnos longos.
    timeout: float = 900.0
    # Teto do texto devolvido ao modelo (o resto vira aviso de truncamento).
    max_output_chars: int = 12000
    # Flags extras repassadas ao pi (ex.: ["--thinking", "high"]).
    extra_args: list[str] = []

    def root(self) -> Path:
        return Path(self.workspace).expanduser().resolve()

    def protected(self) -> list[Path]:
        saida = []
        for item in self.protect:
            if not str(item or "").strip():
                continue
            saida.append(Path(item).expanduser().resolve())
        return saida
