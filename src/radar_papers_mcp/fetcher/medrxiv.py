"""Cliente da API pública do medRxiv.

A API devolve os preprints por intervalo de datas, em páginas de 100. Não há
busca por termo: o filtro por tópico é aplicado localmente sobre título e
abstract, que vêm completos na resposta.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import httpx

from radar_papers_mcp.fetcher.base import Paper

logger = logging.getLogger(__name__)

BASE = "https://api.medrxiv.org/details"
FONTE = "medrxiv"
# Identifica o projeto para quem administra o portal, com link para o repositorio.
# Um coletor publico anonimo e ma cidadania: se algo incomodar do outro lado,
# precisa haver como descobrir o que e e falar com quem mantem.
USER_AGENT = "radar-papers-mcp/0.1 (+https://github.com/fabianofilho/radar-papers-mcp)"
POR_PAGINA = 100
# Teto de páginas por sync (10 mil preprints). Uma semana do medRxiv tem por volta
# de mil; o teto só existe para um backfill muito largo não rodar sem fim.
MAX_PAGINAS = 100


class MedRxivIndisponivel(RuntimeError):
    """A API não respondeu como esperado."""


def _data(bruto: str | None) -> date | None:
    if not bruto:
        return None
    try:
        return datetime.strptime(bruto.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_colecao(corpo: dict[str, object], servidor: str = "medrxiv") -> list[Paper]:
    """Converte a coleção devolvida pela API em papers normalizados."""
    colecao = corpo.get("collection")
    if not isinstance(colecao, list):
        raise MedRxivIndisponivel("resposta sem 'collection'")

    papers: list[Paper] = []
    for item in colecao:
        if not isinstance(item, dict):
            continue
        doi = str(item.get("doi") or "").strip() or None
        titulo = str(item.get("title") or "").strip()
        if not titulo:
            continue
        postado = _data(str(item.get("date") or ""))
        papers.append(
            Paper(
                doi=doi,
                identificador=doi or titulo[:80],
                fonte=FONTE,
                titulo=titulo,
                autores=str(item.get("authors") or "").strip() or None,
                veiculo=f"{servidor} (preprint)",
                data_publicacao=postado,
                abstract=str(item.get("abstract") or "").strip() or None,
                url=f"https://doi.org/{doi}" if doi else "https://www.medrxiv.org/",
                data_entrada=postado,
            )
        )
    return papers


def total_da_janela(corpo: dict[str, object]) -> int | None:
    """O ``messages[0].total`` da resposta: quantos preprints a janela tem."""
    mensagens = corpo.get("messages")
    if not isinstance(mensagens, list) or not mensagens or not isinstance(mensagens[0], dict):
        return None
    try:
        return int(str(mensagens[0].get("total")))
    except ValueError:
        return None


def casa_termos(paper: Paper, termos: tuple[str, ...]) -> bool:
    """Se o título ou o abstract mencionam algum termo do tópico."""
    alvo = f"{paper.titulo} {paper.abstract or ''}".lower()
    return any(termo.strip().lower() in alvo for termo in termos if termo.strip())


class MedRxiv:
    """Preprints por intervalo de datas."""

    def __init__(
        self,
        *,
        servidor: str = "medrxiv",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._servidor = servidor
        self._client = client
        self._client_proprio = client is None

    async def __aenter__(self) -> MedRxiv:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=90.0, headers={"User-Agent": USER_AGENT})
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client is not None and self._client_proprio:
            await self._client.aclose()
            self._client = None

    async def periodo(self, *, dias: int, max_paginas: int = MAX_PAGINAS) -> list[Paper]:
        """Preprints postados nos últimos ``dias``.

        Pagina até o total que a API informa para a janela. A ordem da API não é
        cronológica, então parar antes do fim deixaria buracos em dias quaisquer
        da janela. Se ``max_paginas`` for atingido, registra um aviso no log.
        """
        fim = date.today()
        inicio = fim - timedelta(days=dias)
        papers: list[Paper] = []
        total: int | None = None

        for pagina in range(max_paginas):
            cursor = pagina * POR_PAGINA
            resposta = await self._exigir_client().get(
                f"{BASE}/{self._servidor}/{inicio.isoformat()}/{fim.isoformat()}/{cursor}"
            )
            resposta.raise_for_status()
            corpo = resposta.json()
            atual = parse_colecao(corpo, self._servidor)
            papers.extend(atual)
            if total is None:
                total = total_da_janela(corpo)
            if not atual:
                break
            if total is not None and cursor + POR_PAGINA >= total:
                break
            if total is None and len(atual) < POR_PAGINA:
                break
        else:
            logger.warning(
                "medRxiv: a janela %s a %s tem %s preprints; coletados só %d (max_paginas=%d)",
                inicio,
                fim,
                total if total is not None else "mais de",
                len(papers),
                max_paginas,
            )
        logger.info(
            "medRxiv: %d de %s preprints entre %s e %s",
            len(papers),
            total if total is not None else "?",
            inicio,
            fim,
        )
        return papers

    def _exigir_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("MedRxiv precisa ser usado como 'async with'")
        return self._client
