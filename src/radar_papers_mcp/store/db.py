"""DuckDB com deduplicação por DOI."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

SCHEMA_VERSAO = 1

_DDL = """
CREATE TABLE IF NOT EXISTS papers (
    chave            VARCHAR PRIMARY KEY,
    doi              VARCHAR,
    identificador    VARCHAR NOT NULL,
    fonte            VARCHAR NOT NULL,
    titulo           VARCHAR NOT NULL,
    autores          VARCHAR,
    veiculo          VARCHAR,
    data_publicacao  DATE,
    abstract         VARCHAR,
    url              VARCHAR NOT NULL,
    topicos          VARCHAR,
    data_coleta      TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS resumos (
    chave            VARCHAR PRIMARY KEY,
    topico           VARCHAR NOT NULL,
    problema         VARCHAR NOT NULL,
    metodo           VARCHAR NOT NULL,
    achado_principal VARCHAR NOT NULL,
    relevancia       VARCHAR NOT NULL,
    modelo           VARCHAR NOT NULL,
    resumido_em      TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS schema_meta (
    chave VARCHAR PRIMARY KEY,
    valor VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_papers_data ON papers (data_publicacao);
CREATE INDEX IF NOT EXISTS idx_papers_fonte ON papers (fonte);
"""


class BaseIndisponivel(RuntimeError):
    """A base existe mas está travada, tipicamente um sync em curso."""


def aplicar_schema(conexao: duckdb.DuckDBPyConnection) -> None:
    conexao.execute(_DDL)
    conexao.execute(
        "INSERT INTO schema_meta VALUES ('versao', ?) "
        "ON CONFLICT (chave) DO UPDATE SET valor = excluded.valor",
        [str(SCHEMA_VERSAO)],
    )


@contextmanager
def conectar(
    caminho: Path | str,
    *,
    somente_leitura: bool = False,
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Abre o DuckDB; em leitura não cria nada."""
    em_memoria = str(caminho) == ":memory:"
    if somente_leitura and not em_memoria and not Path(caminho).exists():
        raise FileNotFoundError(f"base local ainda não existe em {caminho}")
    if not somente_leitura and not em_memoria:
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)

    try:
        conexao = duckdb.connect(str(caminho), read_only=somente_leitura and not em_memoria)
    except duckdb.IOException as erro:
        raise BaseIndisponivel(str(erro)) from erro

    try:
        if not somente_leitura:
            aplicar_schema(conexao)
        yield conexao
    finally:
        conexao.close()
