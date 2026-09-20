"""Fixtures compartilhadas."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

from radar_papers_mcp.store.db import aplicar_schema

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def xml_efetch() -> str:
    """Resposta real do efetch do PubMed (capturada em 2026-09-20)."""
    return (FIXTURES / "efetch_pubmed.xml").read_text(encoding="utf-8")


@pytest.fixture
def json_medrxiv() -> dict[str, Any]:
    """Resposta real da API do medRxiv (capturada em 2026-09-20)."""
    return dict(json.loads((FIXTURES / "medrxiv.json").read_text(encoding="utf-8")))


@pytest.fixture
def db(tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    conexao = duckdb.connect(str(tmp_path / "teste.duckdb"))
    aplicar_schema(conexao)
    yield conexao
    conexao.close()


@pytest.fixture
def caminho_db(tmp_path: Path) -> str:
    return str(tmp_path / "tools.duckdb")
