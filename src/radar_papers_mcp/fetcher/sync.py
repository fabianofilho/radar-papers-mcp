"""Busca em PubMed e medRxiv, por tópico, e grava deduplicado."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import duckdb

from radar_papers_mcp.config import Topico
from radar_papers_mcp.fetcher.medrxiv import MedRxiv, casa_termos
from radar_papers_mcp.fetcher.pubmed import PubMed
from radar_papers_mcp.store.queries import gravar

logger = logging.getLogger(__name__)

# Espalha o disparo dentro de meia hora. Num projeto publico isso nao e
# detalhe: horario fixo resolve a concorrencia na maquina de quem roda, mas
# cria concorrencia do outro lado se varias pessoas usarem o padrao do
# .env.example e baterem no mesmo servidor no mesmo minuto.
JITTER_SEGUNDOS = 1800


@dataclass(frozen=True)
class ResultadoSync:
    novos: int
    ja_conhecidos: int
    por_fonte: dict[str, int]


async def sincronizar(
    conexao: duckdb.DuckDBPyConnection,
    topicos: list[Topico],
    *,
    dias: int = 7,
    pubmed_api_key: str | None = None,
    retmax: int = 50,
    incluir_medrxiv: bool = True,
) -> ResultadoSync:
    """Busca cada tópico nas duas fontes e grava.

    O medRxiv é buscado uma vez só, não por tópico: a API não aceita termo, e
    baixar a mesma janela de preprints várias vezes seria desperdício. O filtro
    por tópico é aplicado localmente sobre o resultado único.
    """
    novos = ja_conhecidos = 0
    por_fonte: dict[str, int] = {"pubmed": 0, "medrxiv": 0}

    async with PubMed(api_key=pubmed_api_key) as pubmed:
        for topico in topicos:
            if not topico.pubmed:
                continue
            try:
                papers = await pubmed.buscar(topico.pubmed, dias=dias, retmax=retmax)
            except Exception as erro:  # noqa: BLE001 - uma query ruim não para o sync
                logger.warning("PubMed falhou para %r: %s", topico.nome, erro)
                continue
            por_fonte["pubmed"] += len(papers)
            n, j = gravar(conexao, papers, topico.nome)
            novos += n
            ja_conhecidos += j
            logger.info("PubMed/%s: %d papers (%d novos)", topico.nome, len(papers), n)

    if incluir_medrxiv and any(t.medrxiv for t in topicos):
        async with MedRxiv() as medrxiv:
            try:
                preprints = await medrxiv.periodo(dias=dias)
            except Exception as erro:  # noqa: BLE001
                logger.warning("medRxiv falhou: %s", erro)
                preprints = []

        for topico in topicos:
            if not topico.medrxiv:
                continue
            casados = [p for p in preprints if casa_termos(p, topico.medrxiv)]
            if not casados:
                continue
            por_fonte["medrxiv"] += len(casados)
            n, j = gravar(conexao, casados, topico.nome)
            novos += n
            ja_conhecidos += j
            logger.info("medRxiv/%s: %d preprints (%d novos)", topico.nome, len(casados), n)

    return ResultadoSync(novos=novos, ja_conhecidos=ja_conhecidos, por_fonte=por_fonte)


def agendar_sync(
    caminho_db: str, hora_local: str, topicos: list[Topico], pubmed_api_key: str | None
) -> Any:
    """Agenda a busca num horário fixo e devolve o scheduler iniciado."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from radar_papers_mcp.store.db import conectar

    async def tarefa() -> None:
        try:
            with conectar(caminho_db) as conexao:
                await sincronizar(conexao, topicos, pubmed_api_key=pubmed_api_key)
        except Exception:  # noqa: BLE001 - o agendador não pode morrer por um sync
            logger.exception("sync de papers falhou")

    hora, minuto = (int(p) for p in hora_local.split(":"))
    scheduler = AsyncIOScheduler()
    # jitter: ver JITTER_SEGUNDOS no topo do modulo
    scheduler.add_job(tarefa, "cron", hour=hora, minute=minuto, jitter=JITTER_SEGUNDOS)
    scheduler.start()
    logger.info("sync de papers agendado diariamente às %s", hora_local)
    return scheduler
