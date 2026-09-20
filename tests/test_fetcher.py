"""Fetchers: parsing das respostas reais e rate limiting."""

from __future__ import annotations

import time
from typing import Any

import pytest

from radar_papers_mcp.fetcher.base import LimitadorTaxa, Paper
from radar_papers_mcp.fetcher.medrxiv import MedRxivIndisponivel, casa_termos, parse_colecao
from radar_papers_mcp.fetcher.pubmed import PubMedIndisponivel, parse_efetch


def test_parse_efetch_real(xml_efetch: str) -> None:
    papers = parse_efetch(xml_efetch)
    assert papers
    paper = papers[0]
    assert paper.fonte == "pubmed"
    assert paper.titulo
    assert paper.url.startswith("https://pubmed.ncbi.nlm.nih.gov/")


def test_parse_efetch_extrai_abstract(xml_efetch: str) -> None:
    papers = parse_efetch(xml_efetch)
    assert any(p.abstract for p in papers)


def test_parse_efetch_extrai_data(xml_efetch: str) -> None:
    papers = parse_efetch(xml_efetch)
    assert any(p.data_publicacao is not None for p in papers)


def test_parse_efetch_xml_invalido() -> None:
    with pytest.raises(PubMedIndisponivel):
        parse_efetch("<isso nao fecha")


def test_parse_medrxiv_real(json_medrxiv: dict[str, Any]) -> None:
    papers = parse_colecao(json_medrxiv)
    assert papers
    paper = papers[0]
    assert paper.fonte == "medrxiv"
    assert paper.doi
    assert paper.abstract


def test_parse_medrxiv_sem_colecao() -> None:
    with pytest.raises(MedRxivIndisponivel):
        parse_colecao({"messages": []})


def test_chave_prefere_doi() -> None:
    com_doi = Paper(
        doi="10.1101/ABC",
        identificador="x",
        fonte="medrxiv",
        titulo="t",
        autores=None,
        veiculo=None,
        data_publicacao=None,
        abstract=None,
        url="u",
    )
    sem_doi = Paper(
        doi=None,
        identificador="12345",
        fonte="pubmed",
        titulo="t",
        autores=None,
        veiculo=None,
        data_publicacao=None,
        abstract=None,
        url="u",
    )
    assert com_doi.chave == "10.1101/abc"
    assert sem_doi.chave == "pubmed:12345"


def test_casa_termos_olha_titulo_e_abstract() -> None:
    paper = Paper(
        doi="10.1/x",
        identificador="x",
        fonte="medrxiv",
        titulo="A study on model calibration",
        autores=None,
        veiculo=None,
        data_publicacao=None,
        abstract="We evaluate fairness of clinical models.",
        url="u",
    )
    assert casa_termos(paper, ("calibration",))
    assert casa_termos(paper, ("fairness",))
    assert not casa_termos(paper, ("oncology",))


def test_termo_vazio_nao_casa_tudo() -> None:
    paper = Paper(
        doi=None,
        identificador="x",
        fonte="medrxiv",
        titulo="t",
        autores=None,
        veiculo=None,
        data_publicacao=None,
        abstract=None,
        url="u",
    )
    assert not casa_termos(paper, ("", "  "))


async def test_limitador_espaca_as_chamadas() -> None:
    """O rate limit da NCBI é aplicado de verdade: 3/s sem chave."""
    limitador = LimitadorTaxa(20.0)  # 50ms entre chamadas
    inicio = time.monotonic()
    for _ in range(3):
        await limitador.esperar()
    assert time.monotonic() - inicio >= 0.09  # duas esperas de 50ms


async def test_limitador_desligado_nao_espera() -> None:
    limitador = LimitadorTaxa(0)
    inicio = time.monotonic()
    for _ in range(5):
        await limitador.esperar()
    assert time.monotonic() - inicio < 0.05
