"""Queries sobre a base de papers."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import duckdb

from radar_papers_mcp.fetcher.base import Paper

logger = logging.getLogger(__name__)

_COLUNAS = (
    "chave, doi, identificador, fonte, titulo, autores, veiculo, data_publicacao, "
    "abstract, url, topicos"
)


def _para_dicts(resultado: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    colunas = [d[0] for d in resultado.description or []]
    return [dict(zip(colunas, linha, strict=True)) for linha in resultado.fetchall()]


def gravar(conexao: duckdb.DuckDBPyConnection, papers: list[Paper], topico: str) -> tuple[int, int]:
    """Upsert deduplicado por DOI. Devolve (novos, já existentes).

    Um paper que já estava na base por outro tópico ganha o novo tópico na
    lista, em vez de virar uma segunda linha.
    """
    if not papers:
        return 0, 0

    unicos: dict[str, Paper] = {p.chave: p for p in papers}
    chaves = list(unicos)
    marcadores = ", ".join("?" for _ in chaves)
    existentes = {
        linha[0]: linha[1]
        for linha in conexao.execute(
            f"SELECT chave, topicos FROM papers WHERE chave IN ({marcadores})", chaves
        ).fetchall()
    }

    linhas = []
    for chave, paper in unicos.items():
        anteriores = [t for t in (existentes.get(chave) or "").split("|") if t]
        topicos = anteriores if topico in anteriores else [*anteriores, topico]
        linhas.append(
            [
                chave,
                paper.doi,
                paper.identificador,
                paper.fonte,
                paper.titulo,
                paper.autores,
                paper.veiculo,
                paper.data_publicacao,
                paper.abstract,
                paper.url,
                "|".join(topicos),
            ]
        )

    conexao.executemany(
        f"""
        INSERT INTO papers ({_COLUNAS})
        VALUES ({", ".join("?" for _ in range(11))})
        ON CONFLICT (chave) DO UPDATE SET
            titulo = excluded.titulo,
            autores = excluded.autores,
            veiculo = excluded.veiculo,
            data_publicacao = excluded.data_publicacao,
            abstract = excluded.abstract,
            url = excluded.url,
            topicos = excluded.topicos,
            data_coleta = now()
        """,
        linhas,
    )
    novos = sum(1 for chave in unicos if chave not in existentes)
    return novos, len(unicos) - novos


def papers_do_periodo(
    conexao: duckdb.DuckDBPyConnection,
    *,
    dias: int,
    topico: str | None = None,
    limite: int = 50,
) -> list[dict[str, Any]]:
    """Papers publicados no período, opcionalmente filtrados por tópico."""
    corte = date.today() - timedelta(days=dias)
    filtro = "AND topicos LIKE ?" if topico else ""
    parametros: list[Any] = [corte]
    if topico:
        parametros.append(f"%{topico}%")
    parametros.append(limite)
    return _para_dicts(
        conexao.execute(
            f"""
            SELECT {_COLUNAS}
            FROM papers
            WHERE data_publicacao >= ? {filtro}
            ORDER BY data_publicacao DESC, chave
            LIMIT ?
            """,
            parametros,
        )
    )


def por_chave(conexao: duckdb.DuckDBPyConnection, chave: str) -> dict[str, Any] | None:
    linhas = _para_dicts(
        conexao.execute(f"SELECT {_COLUNAS} FROM papers WHERE chave = ?", [chave.lower()])
    )
    return linhas[0] if linhas else None


def resumo_em_cache(conexao: duckdb.DuckDBPyConnection, chave: str) -> dict[str, Any] | None:
    linhas = _para_dicts(
        conexao.execute(
            "SELECT chave, topico, problema, metodo, achado_principal, relevancia, modelo "
            "FROM resumos WHERE chave = ?",
            [chave.lower()],
        )
    )
    return linhas[0] if linhas else None


def gravar_resumo(
    conexao: duckdb.DuckDBPyConnection,
    *,
    chave: str,
    topico: str,
    problema: str,
    metodo: str,
    achado_principal: str,
    relevancia: str,
    modelo: str,
) -> None:
    conexao.execute(
        """
        INSERT INTO resumos (chave, topico, problema, metodo, achado_principal, relevancia, modelo)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (chave) DO UPDATE SET
            topico = excluded.topico,
            problema = excluded.problema,
            metodo = excluded.metodo,
            achado_principal = excluded.achado_principal,
            relevancia = excluded.relevancia,
            modelo = excluded.modelo,
            resumido_em = now()
        """,
        [chave.lower(), topico, problema, metodo, achado_principal, relevancia, modelo],
    )
