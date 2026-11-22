"""Leitura do progresso de estudos (Abin Suite / Supabase) para a Gaia.

O Abin Suite guarda tudo numa tabela ``study_items`` com uma linha por
registro sincronizavel (``collection``: results, goals, review, ...). Aqui so
lemos: nenhuma escrita no banco do app, para nao brigar com o sync do navegador.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from gaia.config import EstudosConfig

TABLE = "study_items"


class EstudosError(RuntimeError):
    pass


def _load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return env
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


class EstudosClient:
    def __init__(self, cfg: EstudosConfig):
        self.cfg = cfg
        self._env = _load_env_file(Path(cfg.env_file).expanduser())

    def _get(self, *names: str) -> str:
        for name in names:
            value = self._env.get(name) or os.environ.get(name)
            if value:
                return value
        return ""

    @property
    def enabled(self) -> bool:
        if not self.cfg.enabled:
            return False
        return bool(
            self._get("VITE_SUPABASE_URL", "SUPABASE_URL")
            and self._get("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY")
            and self._get("ESTUDOS_USER_ID")
        )

    def _headers(self) -> dict[str, str]:
        key = self._get("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY")
        headers = {"apikey": key, "Content-Type": "application/json"}
        if key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def _query(self, params: dict[str, str]) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        base = self._get("VITE_SUPABASE_URL", "SUPABASE_URL").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{base}/rest/v1/{TABLE}", headers=self._headers(), params=params
                )
        except httpx.HTTPError as exc:
            raise EstudosError(f"falha ao consultar os estudos: {exc}") from exc
        if not response.is_success:
            raise EstudosError(f"estudos respondeu {response.status_code}")
        data = response.json()
        return data if isinstance(data, list) else []

    def _user_filter(self) -> dict[str, str]:
        return {"user_id": f"eq.{self._get('ESTUDOS_USER_ID')}"}

    async def results(self) -> list[dict[str, Any]]:
        """Tentativas de simulado (collection 'results'), com o payload cru."""
        params = {
            **self._user_filter(),
            "collection": "eq.results",
            "select": "item_id,payload,updated_at",
            "limit": "2000",
        }
        return await self._query(params)

    async def goals(self) -> dict[str, Any]:
        """Metas semanais (collection 'goals', item unico)."""
        params = {
            **self._user_filter(),
            "collection": "eq.goals",
            "item_id": "eq.goals",
            "select": "payload",
            "limit": "1",
        }
        rows = await self._query(params)
        if not rows:
            return {}
        payload = rows[0].get("payload")
        return payload if isinstance(payload, dict) else {}

    async def reviews(self) -> list[dict[str, Any]]:
        """Itens do caderno de erros (collection 'review') com dueAt em ms."""
        params = {
            **self._user_filter(),
            "collection": "eq.review",
            "select": "item_id,payload,updated_at",
            "limit": "5000",
        }
        return await self._query(params)
