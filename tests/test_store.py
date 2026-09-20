"""Armazenamento: deduplicação por DOI e acúmulo de tópicos."""

from __future__ import annotations

from datetime import date, timedelta

import duckdb

from radar_papers_mcp.fetcher.base import Paper
from radar_papers_mcp.store.queries import gravar, papers_do_periodo, por_chave


def _paper(**campos: object) -> Paper:
    base = {
        "doi": "10.1101/abc",
        "identificador": "abc",
        "fonte": "medrxiv",
        "titulo": "Um estudo",
        "autores": "Silva J",
        "veiculo": "medrxiv (preprint)",
        "data_publicacao": date.today() - timedelta(days=2),
        "abstract": "Um abstract.",
        "url": "https://doi.org/10.1101/abc",
    }
    base.update(campos)
    return Paper(**base)  # type: ignore[arg-type]


def test_dedup_por_doi(db: duckdb.DuckDBPyConnection) -> None:
    """O mesmo DOI vindo de fontes diferentes é o mesmo paper."""
    assert gravar(db, [_paper()], "calibração") == (1, 0)
    assert gravar(db, [_paper(fonte="pubmed", identificador="999")], "calibração") == (0, 1)
    assert db.execute("SELECT count(*) FROM papers").fetchone()[0] == 1


def test_paper_acumula_topicos(db: duckdb.DuckDBPyConnection) -> None:
    """Casar com dois tópicos não pode gerar duas linhas."""
    gravar(db, [_paper()], "calibração")
    gravar(db, [_paper()], "fairness")
    linha = por_chave(db, "10.1101/abc")
    assert linha is not None
    assert set(linha["topicos"].split("|")) == {"calibração", "fairness"}


def test_topico_repetido_nao_duplica(db: duckdb.DuckDBPyConnection) -> None:
    gravar(db, [_paper()], "calibração")
    gravar(db, [_paper()], "calibração")
    linha = por_chave(db, "10.1101/abc")
    assert linha is not None
    assert linha["topicos"] == "calibração"


def test_sem_doi_usa_id_da_fonte(db: duckdb.DuckDBPyConnection) -> None:
    gravar(db, [_paper(doi=None, identificador="777", fonte="pubmed")], "x")
    assert por_chave(db, "pubmed:777") is not None


def test_periodo_exclui_antigos(db: duckdb.DuckDBPyConnection) -> None:
    gravar(db, [_paper()], "x")
    gravar(
        db,
        [_paper(doi="10.1/velho", data_publicacao=date.today() - timedelta(days=400))],
        "x",
    )
    assert len(papers_do_periodo(db, dias=30)) == 1


def test_filtro_por_topico(db: duckdb.DuckDBPyConnection) -> None:
    gravar(db, [_paper()], "calibração")
    gravar(db, [_paper(doi="10.1/outro")], "fairness")
    achados = papers_do_periodo(db, dias=30, topico="fairness")
    assert [a["doi"] for a in achados] == ["10.1/outro"]


def test_lista_vazia_nao_quebra(db: duckdb.DuckDBPyConnection) -> None:
    assert gravar(db, [], "x") == (0, 0)
