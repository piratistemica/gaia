"""Control Center: camada de gerencia sobre o nucleo da Gaia.

Nada aqui conhece HTTP, asyncio de servidor ou a UI. Sao funcoes puras (ou
quase) que recebem ``Config``/``MemoryStore`` e devolvem dicionarios prontos
para JSON. A regra do repo continua valendo: um unico processo escritor, um
unico SQLite, segredos so no ``.env``.

Este pacote NAO agenda nada. Ele observa o que o ``Orchestrator`` executa e
descreve o que esta configurado; a Fase C transforma essa descricao em
governanca (ver ``docs/plans/2026-11-22-control-center-fases-cde.md``).
"""

from gaia.control.runs import record_run

__all__ = ["record_run"]
