"""`instances.json`: as maquinas onde este `gaia serve` pode estar.

Um arquivo manual em ``GAIA_DATA_DIR`` (``~/.local/share/gaia/instances.json``)
com a lista de motores que o painel/web podem alcançar::

    [
      {
        "id": "mini",
        "name": "Mini PC",
        "url": "http://mini.local:8787",
        "serve_token": "...",
        "control_token": "..."
      }
    ]

Regras que este modulo garante:

* **o token nunca sai daqui inteiro.** ``Instance.public()`` e o que vai para o
  navegador: ``id``/``name``/``url`` e se existe credencial, nada mais. O
  arquivo e ``chmod 600`` (contem segredos) e nao e servido como asset.
* **arquivo quebrado nao derruba o servidor.** Ausente, JSON invalido ou item
  sem ``id``/``url`` viram lista vazia/parcial: a tela funciona sem seletor.
* **status online/offline e medido aqui**, com ``GET <url>/health`` em paralelo
  e timeout curto. O navegador nunca fala com outra origem (sem CORS, sem
  token em URL) e um host fora do ar nao vira erro de rota.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

FILENAME = "instances.json"
# Curto de proposito: o seletor e carregado junto com o chat, nao pode esperar
# um host morto. Todas as instancias sao sondadas em paralelo.
HEALTH_TIMEOUT = 0.8
MAX_PROBES = 8


@dataclass(frozen=True)
class Instance:
    """Uma instancia do motor. Os tokens ficam so no servidor."""

    id: str
    name: str
    url: str
    serve_token: str = ""
    control_token: str = ""
    current: bool = False

    def public(self) -> dict[str, Any]:
        """Versao para o navegador: sem token, so a existencia da credencial."""
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "current": self.current,
            "has_serve_token": bool(self.serve_token),
            "has_control_token": bool(self.control_token),
        }

    def header(self) -> dict[str, str]:
        """Cabecalho para falar com essa instancia (Bearer do chat dela)."""
        if not self.serve_token:
            return {}
        return {"Authorization": f"Bearer {self.serve_token}"}


# Host aceito: DNS simples/IPv4 ou IPv6 literal. Qualquer outra coisa e
# recusada — ``javascript:alert(1)`` sem esquema viraria
# ``http://javascript:alert(1)`` na validacao ingenua.
_HOST = re.compile(r"^(?:[A-Za-z0-9._-]+|[0-9A-Fa-f:.]+)$")


def normalize_url(value: str) -> str:
    """URL http(s) canonica sem barra final. ``""`` = invalida.

    So ``http``/``https``: um ``file://`` ou ``javascript:`` no arquivo viraria
    leitura local/execucao no navegador de quem abrisse o seletor.
    """
    texto = (value or "").strip()
    if not texto:
        return ""
    if "://" not in texto:
        texto = "http://" + texto
    partes = urlparse(texto)
    if partes.scheme not in {"http", "https"} or not partes.netloc:
        return ""
    if partes.username or partes.password:  # credencial na URL vaza em log
        return ""
    if not partes.hostname or not _HOST.match(partes.hostname):
        return ""
    try:
        porta = partes.port
    except ValueError:  # porta nao numerica / fora da faixa
        return ""
    if porta is not None and not 1 <= porta <= 65535:
        return ""
    return texto.rstrip("/")


def config_path(cfg: Any = None) -> Path:
    """Caminho do arquivo: ``resolved_data_dir()`` da config, env ou padrao."""
    if cfg is not None:
        resolved = getattr(cfg, "resolved_data_dir", None)
        if callable(resolved):
            return Path(resolved()) / FILENAME
    from gaia.config import Config

    return Config.default_data_dir() / FILENAME


def _parse_item(dado: Any) -> Instance | None:
    if not isinstance(dado, dict):
        return None
    ident = str(dado.get("id") or "").strip()
    url = normalize_url(str(dado.get("url") or ""))
    if not ident or not url:
        return None
    return Instance(
        id=ident,
        name=str(dado.get("name") or ident).strip() or ident,
        url=url,
        serve_token=str(dado.get("serve_token") or "").strip(),
        control_token=str(dado.get("control_token") or "").strip(),
    )


def load(path: Path | None = None, *, cfg: Any = None) -> list[Instance]:
    """Lista de instancias do arquivo. Nunca levanta: erro vira lista vazia."""
    alvo = Path(path) if path is not None else config_path(cfg)
    try:
        bruto = alvo.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:  # permissao, diretorio no lugar do arquivo
        logger.warning("instances.json ilegivel (%s): %s", alvo, exc)
        return []
    try:
        dados = json.loads(bruto)
    except ValueError as exc:
        logger.warning("instances.json invalido (%s): %s", alvo, exc)
        return []
    if not isinstance(dados, list):
        logger.warning("instances.json deveria ser uma lista (%s)", alvo)
        return []
    vistas: set[str] = set()
    itens: list[Instance] = []
    for dado in dados:
        item = _parse_item(dado)
        if item is None or item.id in vistas:
            continue
        vistas.add(item.id)
        itens.append(item)
    return itens


def _mesmo_endereco(url: str, host_header: str) -> bool:
    """A instancia e este servidor? Compara host (e porta quando explicita).

    Serve para marcar ``current`` sem exigir configuracao extra: quem abriu a
    pagina por ``http://mini.local:8787`` ve o mini como atual. Atras de proxy
    ou IP do tailnet pode nao casar — nesse caso o front mostra so a URL dele.
    """
    alvo = urlparse(url)
    atual = (host_header or "").strip().lower()
    if not atual:
        return False
    if ":" in alvo.netloc:
        return alvo.netloc.lower() == atual
    return alvo.hostname is not None and alvo.hostname.lower() == atual.split(":")[0]


def marcar_atual(itens: list[Instance], host_header: str, instance_id: str = "") -> list[Instance]:
    """Devolve a lista com ``current`` marcado (config vence o Host header)."""
    if instance_id:
        return [
            Instance(**{**_campos(item), "current": item.id == instance_id})
            for item in itens
        ]
    return [
        Instance(**{**_campos(item), "current": _mesmo_endereco(item.url, host_header)})
        for item in itens
    ]


def _campos(item: Instance) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "url": item.url,
        "serve_token": item.serve_token,
        "control_token": item.control_token,
        "current": item.current,
    }


def probe(item: Instance, timeout: float = HEALTH_TIMEOUT) -> dict[str, Any]:
    """``GET <url>/health`` com o token da instancia. Nunca levanta.

    Devolve ``online``, ``latency_ms`` e, quando da certo, ``model``/``version``
    do motor remoto — o suficiente para o seletor dizer *o que* esta ali.
    """
    inicio = time.monotonic()
    pedido = urllib.request.Request(
        f"{item.url}/health", headers={"Accept": "application/json", **item.header()}
    )
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
            corpo = json.loads(resposta.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return {
            "online": False,
            "latency_ms": _latencia(inicio),
            "error": f"HTTP {exc.code}",
        }
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {
            "online": False,
            "latency_ms": _latencia(inicio),
            "error": str(getattr(exc, "reason", exc))[:200],
        }
    if not isinstance(corpo, dict):
        return {"online": False, "latency_ms": _latencia(inicio), "error": "resposta invalida"}
    return {
        "online": True,
        "latency_ms": _latencia(inicio),
        "model": str(corpo.get("model") or ""),
        "version": str(corpo.get("version") or ""),
    }


def _latencia(inicio: float) -> int:
    return max(0, int((time.monotonic() - inicio) * 1000))


def describe(
    itens: list[Instance],
    *,
    probe_fn: Callable[[Instance, float], dict[str, Any]] | None = None,
    timeout: float = HEALTH_TIMEOUT,
) -> list[dict[str, Any]]:
    """Lista pronta para a tela: dados publicos + status medido em paralelo."""
    if not itens:
        return []
    funcao = probe_fn or probe
    with ThreadPoolExecutor(max_workers=min(MAX_PROBES, len(itens))) as pool:
        futuros = [pool.submit(funcao, item, timeout) for item in itens]
        estados = []
        for futuro in futuros:
            try:
                estados.append(futuro.result())
            except Exception as exc:  # sonda quebrada = offline, nao 500
                logger.warning("sonda de instancia falhou: %s", exc)
                estados.append({"online": False, "error": str(exc)[:200]})
    return [{**item.public(), **estado} for item, estado in zip(itens, estados, strict=True)]


__all__ = [
    "FILENAME",
    "HEALTH_TIMEOUT",
    "Instance",
    "config_path",
    "describe",
    "load",
    "marcar_atual",
    "normalize_url",
    "probe",
]
