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
    "abstract, url, topicos, data_entrada"
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

    hoje = date.today()
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
                # Sem data de entrada informada pela fonte, vale o dia da 1ª coleta.
                paper.data_entrada or hoje,
            ]
        )

    conexao.executemany(
        f"""
        INSERT INTO papers ({_COLUNAS})
        VALUES ({", ".join("?" for _ in range(12))})
        ON CONFLICT (chave) DO UPDATE SET
            titulo = excluded.titulo,
            autores = excluded.autores,
            veiculo = excluded.veiculo,
            data_publicacao = excluded.data_publicacao,
            abstract = excluded.abstract,
            url = excluded.url,
            topicos = excluded.topicos,
            -- A entrada nunca anda para frente: um preprint que depois sai no
            -- PubMed com o mesmo DOI não volta a aparecer como novo.
            data_entrada = least(papers.data_entrada, excluded.data_entrada),
            data_coleta = now()
        """,
        linhas,
    )
    novos = sum(1 for chave in unicos if chave not in existentes)
    return novos, len(unicos) - novos


def ids_pubmed(conexao: duckdb.DuckDBPyConnection) -> list[str]:
    """PMIDs de todos os papers do PubMed na base."""
    linhas = conexao.execute(
        "SELECT identificador FROM papers WHERE fonte = 'pubmed' ORDER BY identificador"
    ).fetchall()
    return [str(linha[0]) for linha in linhas]


def corrigir_data_entrada(conexao: duckdb.DuckDBPyConnection, papers: list[Paper]) -> int:
    """Grava a data de entrada informada pela fonte, sobrescrevendo a aproximação.

    Serve para as linhas migradas do schema 1, em que a data de entrada do PubMed
    foi aproximada pelo dia da coleta. Devolve quantas linhas mudaram.
    """
    pares = [
        [p.data_entrada, p.fonte, p.identificador] for p in papers if p.data_entrada is not None
    ]
    if not pares:
        return 0
    mudaram = 0
    for data, fonte, identificador in pares:
        linha = conexao.execute(
            "UPDATE papers SET data_entrada = ? "
            "WHERE fonte = ? AND identificador = ? AND data_entrada IS DISTINCT FROM ? "
            "RETURNING chave",
            [data, fonte, identificador, data],
        ).fetchall()
        mudaram += len(linha)
    return mudaram


def _tem_coluna(conexao: duckdb.DuckDBPyConnection, coluna: str) -> bool:
    linha = conexao.execute(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'papers' AND column_name = ?",
        [coluna],
    ).fetchone()
    return bool(linha and linha[0])


def _colunas_de(conexao: duckdb.DuckDBPyConnection) -> str:
    """Colunas legíveis: uma base do schema 1 ainda não migrada não tem data_entrada.

    A migração roda na próxima conexão de escrita (o sync, por exemplo); até lá o
    servidor, que abre a base só para leitura, continua funcionando.
    """
    if _tem_coluna(conexao, "data_entrada"):
        return _COLUNAS
    return _COLUNAS.replace(", data_entrada", "")


def topicos_na_base(conexao: duckdb.DuckDBPyConnection) -> list[str]:
    """Nomes dos tópicos que têm pelo menos um paper na base."""
    linhas = conexao.execute(
        "SELECT DISTINCT unnest(string_split(topicos, '|')) AS t FROM papers "
        "WHERE topicos IS NOT NULL ORDER BY t"
    ).fetchall()
    return [str(linha[0]) for linha in linhas if linha[0]]


def papers_do_periodo(
    conexao: duckdb.DuckDBPyConnection,
    *,
    dias: int,
    topico: str | None = None,
    limite: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """Papers que entraram na fonte no período. Devolve (página, total do período).

    O filtro é pela data de entrada na fonte, não pela data da edição da revista.
    ``topico`` precisa ser o nome exato de um tópico gravado (igualdade, não
    substring: "calibração" não casa com "multicalibração").
    """
    corte = date.today() - timedelta(days=dias)
    colunas = _colunas_de(conexao)
    data = "data_entrada" if "data_entrada" in colunas else "data_publicacao"
    filtro = "AND list_contains(string_split(topicos, '|'), ?)" if topico else ""
    parametros: list[Any] = [corte]
    if topico:
        parametros.append(topico)
    contagem = conexao.execute(
        f"SELECT count(*) FROM papers WHERE {data} >= ? {filtro}", parametros
    ).fetchone()
    total = int(contagem[0]) if contagem else 0
    linhas = _para_dicts(
        conexao.execute(
            f"""
            SELECT {colunas}
            FROM papers
            WHERE {data} >= ? {filtro}
            ORDER BY {data} DESC, chave
            LIMIT ?
            """,
            [*parametros, limite],
        )
    )
    return linhas, total


def por_chave(conexao: duckdb.DuckDBPyConnection, chave: str) -> dict[str, Any] | None:
    linhas = _para_dicts(
        conexao.execute(
            f"SELECT {_colunas_de(conexao)} FROM papers WHERE chave = ?", [chave.lower()]
        )
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
