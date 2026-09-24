"""Armazenamento: deduplicação por DOI e acúmulo de tópicos."""

from __future__ import annotations

from datetime import date, timedelta

import duckdb

from radar_papers_mcp.fetcher.base import Paper
from radar_papers_mcp.store.db import aplicar_schema
from radar_papers_mcp.store.queries import (
    corrigir_data_entrada,
    gravar,
    ids_pubmed,
    papers_do_periodo,
    por_chave,
    topicos_na_base,
)


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
        [
            _paper(
                doi="10.1/velho",
                data_publicacao=date.today() - timedelta(days=400),
                data_entrada=date.today() - timedelta(days=400),
            )
        ],
        "x",
    )
    linhas, total = papers_do_periodo(db, dias=30)
    assert len(linhas) == 1
    assert total == 1


def test_filtro_por_topico(db: duckdb.DuckDBPyConnection) -> None:
    gravar(db, [_paper()], "calibração")
    gravar(db, [_paper(doi="10.1/outro")], "fairness")
    achados, _ = papers_do_periodo(db, dias=30, topico="fairness")
    assert [a["doi"] for a in achados] == ["10.1/outro"]


def test_lista_vazia_nao_quebra(db: duckdb.DuckDBPyConnection) -> None:
    assert gravar(db, [], "x") == (0, 0)


# --- L1: "novo" é pela data de entrada na fonte --------------------------------


def test_periodo_usa_data_de_entrada_e_nao_a_da_edicao(db: duckdb.DuckDBPyConnection) -> None:
    """Edição datada no futuro ou meses atrás não decide o que é novo."""
    hoje = date.today()
    gravar(
        db,
        [
            # Entrou há 3 dias, edição impressa só daqui a 3 meses.
            _paper(
                doi="10.1/futuro",
                data_publicacao=hoje + timedelta(days=90),
                data_entrada=hoje - timedelta(days=3),
            ),
            # Edição "01-01" do ano (revista só informou o ano), entrou ontem.
            _paper(
                doi="10.1/so-ano",
                data_publicacao=date(hoje.year, 1, 1),
                data_entrada=hoje - timedelta(days=1),
            ),
            # Edição recente, mas entrou no PubMed há um ano.
            _paper(
                doi="10.1/antigo",
                data_publicacao=hoje - timedelta(days=1),
                data_entrada=hoje - timedelta(days=365),
            ),
        ],
        "x",
    )
    linhas, total = papers_do_periodo(db, dias=7)
    assert [linha["doi"] for linha in linhas] == ["10.1/so-ano", "10.1/futuro"]
    assert total == 2


def test_sem_data_de_entrada_vale_a_primeira_coleta(db: duckdb.DuckDBPyConnection) -> None:
    gravar(db, [_paper(data_entrada=None)], "x")
    linha = por_chave(db, "10.1101/abc")
    assert linha is not None
    assert linha["data_entrada"] == date.today()


def test_data_de_entrada_nao_anda_para_frente(db: duckdb.DuckDBPyConnection) -> None:
    """O preprint que depois sai no PubMed com o mesmo DOI não volta como novo."""
    antes = date.today() - timedelta(days=40)
    gravar(db, [_paper(data_entrada=antes)], "x")
    gravar(db, [_paper(fonte="pubmed", data_entrada=date.today())], "x")
    linha = por_chave(db, "10.1101/abc")
    assert linha is not None
    assert linha["data_entrada"] == antes


def test_migracao_do_schema_1() -> None:
    """Base antiga sem data_entrada: a migração cria a coluna e preenche."""
    conexao = duckdb.connect(":memory:")
    conexao.execute(
        """
        CREATE TABLE papers (
            chave VARCHAR PRIMARY KEY, doi VARCHAR, identificador VARCHAR NOT NULL,
            fonte VARCHAR NOT NULL, titulo VARCHAR NOT NULL, autores VARCHAR,
            veiculo VARCHAR, data_publicacao DATE, abstract VARCHAR, url VARCHAR NOT NULL,
            topicos VARCHAR, data_coleta TIMESTAMP DEFAULT current_timestamp
        );
        CREATE INDEX idx_papers_data ON papers (data_publicacao);
        INSERT INTO papers (chave, identificador, fonte, titulo, url, data_publicacao, data_coleta)
        VALUES ('m', 'm', 'medrxiv', 't', 'u', DATE '2026-09-01', TIMESTAMP '2026-09-20 04:00'),
               ('p', 'p', 'pubmed', 't', 'u', DATE '2026-12-01', TIMESTAMP '2026-09-21 04:00');
        """
    )
    # Antes de migrar, a leitura continua funcionando pela data antiga.
    linhas, _ = papers_do_periodo(conexao, dias=3650)
    assert {linha["chave"] for linha in linhas} == {"m", "p"}

    aplicar_schema(conexao)
    datas = dict(conexao.execute("SELECT chave, data_entrada FROM papers").fetchall())
    assert datas == {"m": date(2026, 9, 1), "p": date(2026, 9, 21)}
    aplicar_schema(conexao)  # idempotente
    conexao.close()


# --- N2: tópico por igualdade ----------------------------------------------------


def test_topico_nao_casa_por_substring(db: duckdb.DuckDBPyConnection) -> None:
    gravar(db, [_paper()], "multicalibração")
    gravar(db, [_paper(doi="10.1/outro")], "calibração")
    achados, total = papers_do_periodo(db, dias=30, topico="calibração")
    assert [a["doi"] for a in achados] == ["10.1/outro"]
    assert total == 1


def test_topicos_na_base(db: duckdb.DuckDBPyConnection) -> None:
    gravar(db, [_paper()], "b")
    gravar(db, [_paper()], "a")
    assert topicos_na_base(db) == ["a", "b"]


def test_corrigir_data_entrada_do_pubmed(db: duckdb.DuckDBPyConnection) -> None:
    """Linha migrada com a data aproximada ganha a data Entrez real."""
    aproximada = date.today()
    real = date.today() - timedelta(days=200)
    gravar(db, [_paper(doi=None, fonte="pubmed", identificador="42", data_entrada=aproximada)], "x")
    assert ids_pubmed(db) == ["42"]
    relido = _paper(doi=None, fonte="pubmed", identificador="42", data_entrada=real)
    assert corrigir_data_entrada(db, [relido]) == 1
    assert corrigir_data_entrada(db, [relido]) == 0
    linha = por_chave(db, "pubmed:42")
    assert linha is not None and linha["data_entrada"] == real
    assert papers_do_periodo(db, dias=7) == ([], 0)
