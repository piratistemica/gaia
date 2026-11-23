"""Ponte com o pi (harness de codigo) rodando em outro workspace.

O pi e um agente de codigo externo: ele le/escreve arquivos e roda shell com
as permissoes do usuario. Aqui so mora o *runner* (processo + JSONL) e a
configuracao; a ferramenta exposta ao modelo fica em `gaia/agent/tools/pi.py`.
"""

from __future__ import annotations

from gaia.pi.config import PiConfig
from gaia.pi.runner import PiError, PiResult, PiRunner

__all__ = ["PiConfig", "PiError", "PiResult", "PiRunner"]
