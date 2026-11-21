"""Embeddings para o Second Brain: semelhanca por sentido, nao por palavra.

A busca atual (`SecondBrainIndex.search`) casa tokens: "impulsão" hoje nao
encontra "vontade" de um mes atras. Com vetores, encontra.

Este módulo e deliberadamente pequeno e **opcional**:

- ``NullEmbedder`` (padrao): ``enabled = False``. Nada muda, a busca segue
  lexical. Nenhuma nota depende de embedding para existir.
- ``OpenAIEmbedder``: fala o dialeto ``POST {base_url}/embeddings``, o mesmo de
  OpenAI, Ollama (``/v1``), LM Studio e qualquer gateway compativel. Basta
  apontar ``base_url``/``model`` e (se preciso) a chave.

A chamada e **sincrona** de proposito: o indice e sincrono e roda dentro de
``asyncio.to_thread``. Misturar clientes assincronos aqui so criaria um laco
emprestado.
"""

from __future__ import annotations

import array
import logging
import math
from typing import Protocol

import httpx

# Lote por requisicao. Em CPU (mini PC) um lote grande passa facil dos 30s:
# 8 trechos por chamada e o que mantem cada requisicao curta.
_DEFAULT_BATCH = 8
_DEFAULT_TIMEOUT = 120.0


class Embedder(Protocol):
    enabled: bool
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class NullEmbedder:
    """Sem embedding configurado: tudo continua como era."""

    enabled = False
    model = ""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return []


class OpenAIEmbedder:
    """Cliente de ``POST {base_url}/embeddings`` (OpenAI e compativeis)."""

    enabled = True

    def __init__(
        self,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
        batch_size: int = _DEFAULT_BATCH,
        logger: logging.Logger | None = None,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.batch_size = max(1, int(batch_size))
        self.logger = logger or logging.getLogger(__name__)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Vetores na ordem de ``texts``; lista vazia se o provedor falhar.

        Falhar aqui nunca pode derrubar um sonho ou uma indexacao: quem chama
        cai na busca lexical.
        """
        clean = [text if (text or "").strip() else "vazio" for text in texts]
        vectors: list[list[float]] = []
        try:
            with httpx.Client(timeout=self.timeout) as client:
                for start in range(0, len(clean), self.batch_size):
                    batch = clean[start : start + self.batch_size]
                    response = client.post(
                        f"{self.base_url}/embeddings",
                        json={"model": self.model, "input": batch},
                        headers=self._headers(),
                    )
                    response.raise_for_status()
                    data = response.json().get("data") or []
                    data.sort(key=lambda item: item.get("index", 0))
                    vectors.extend([list(item.get("embedding") or []) for item in data])
        except Exception as exc:  # pragma: no cover - depende do provedor
            self.logger.warning("embeddings falharam (%s): %s", self.model, exc)
            return []
        if len(vectors) != len(texts):
            self.logger.warning(
                "embeddings: %d vetores para %d textos", len(vectors), len(texts)
            )
            return []
        return vectors

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}


def build_embedder(
    model: str,
    base_url: str = "",
    api_key: str = "",
    timeout: float = _DEFAULT_TIMEOUT,
    batch_size: int = _DEFAULT_BATCH,
    logger: logging.Logger | None = None,
) -> Embedder:
    """Embedder pela config: modelo vazio significa desligado."""
    if not (model or "").strip():
        return NullEmbedder()
    return OpenAIEmbedder(
        model=model.strip(),
        base_url=(base_url or "https://api.openai.com/v1").strip(),
        api_key=api_key,
        timeout=timeout,
        batch_size=batch_size,
        logger=logger,
    )


def pack(vector: list[float]) -> bytes:
    """float32 empacotado: 4 bytes por dimensao, sem JSON no banco."""
    return array.array("f", vector).tobytes()


def unpack(blob: bytes) -> list[float]:
    values = array.array("f")
    values.frombytes(blob)
    return list(values)


def cosine(a: list[float], b: list[float]) -> float:
    """Similaridade de cosseno, sem numpy (indice pequeno, uma vez por noite)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))
