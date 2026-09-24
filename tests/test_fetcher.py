"""Fetchers: parsing das respostas reais e rate limiting."""

from __future__ import annotations

import time
from datetime import date
from typing import Any

import httpx
import pytest
import respx

from radar_papers_mcp.fetcher.base import LimitadorTaxa, Paper
from radar_papers_mcp.fetcher.medrxiv import (
    BASE as BASE_MEDRXIV,
)
from radar_papers_mcp.fetcher.medrxiv import (
    MedRxiv,
    MedRxivIndisponivel,
    casa_termos,
    parse_colecao,
)
from radar_papers_mcp.fetcher.pubmed import BASE as BASE_PUBMED
from radar_papers_mcp.fetcher.pubmed import PubMed, PubMedIndisponivel, parse_efetch


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


# --- L1: data de entrada no PubMed ----------------------------------------------


def test_parse_efetch_data_de_entrada_vem_do_entrez(xml_efetch: str) -> None:
    """Na fixture real a edição é 2026-05 (só ano e mês), mas a entrada foi 2025-10-07."""
    paper = parse_efetch(xml_efetch)[0]
    assert paper.data_publicacao == date(2026, 5, 1)
    assert paper.data_entrada == date(2025, 10, 7)


def _artigo(pubdate: str, historia: str) -> str:
    return (
        "<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID><Article>"
        f"<Journal><JournalIssue><PubDate>{pubdate}</PubDate></JournalIssue>"
        "<Title>J</Title></Journal><ArticleTitle>T</ArticleTitle></Article>"
        f"</MedlineCitation><PubmedData><History>{historia}</History></PubmedData>"
        "</PubmedArticle></PubmedArticleSet>"
    )


def test_parse_efetch_so_ano_e_epub_ahead_of_print() -> None:
    xml = _artigo(
        "<Year>2027</Year>",
        '<PubMedPubDate PubStatus="pubmed"><Year>2026</Year><Month>9</Month>'
        "<Day>20</Day></PubMedPubDate>",
    )
    paper = parse_efetch(xml)[0]
    assert paper.data_publicacao == date(2027, 1, 1)
    # Sem 'entrez', cai para 'pubmed'.
    assert paper.data_entrada == date(2026, 9, 20)


def test_parse_efetch_sem_historia() -> None:
    paper = parse_efetch(_artigo("<Year>2026</Year><Month>Sep</Month>", ""))[0]
    assert paper.data_entrada is None


def test_parse_medrxiv_entrada_e_a_postagem(json_medrxiv: dict[str, Any]) -> None:
    paper = parse_colecao(json_medrxiv)[0]
    assert paper.data_entrada is not None
    assert paper.data_entrada == paper.data_publicacao


# --- N1: paginação sem truncar em silêncio --------------------------------------


def _preprint(i: int) -> dict[str, str]:
    return {
        "doi": f"10.1101/{i}",
        "title": f"Preprint {i}",
        "authors": "A",
        "date": date.today().isoformat(),
        "abstract": "x",
    }


def _pagina_medrxiv(cursor: int, total: int) -> dict[str, Any]:
    fim = min(cursor + 100, total)
    return {
        "messages": [{"status": "ok", "cursor": cursor, "total": str(total)}],
        "collection": [_preprint(i) for i in range(cursor, fim)],
    }


@respx.mock
async def test_medrxiv_pagina_ate_o_total() -> None:
    """Janela com 1042 preprints: antes o teto de 5 páginas parava em 500."""
    total = 1042

    def responder(request: httpx.Request) -> httpx.Response:
        cursor = int(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json=_pagina_medrxiv(cursor, total))

    rota = respx.get(url__startswith=BASE_MEDRXIV).mock(side_effect=responder)
    async with MedRxiv() as medrxiv:
        papers = await medrxiv.periodo(dias=7)
    assert len(papers) == total
    assert rota.call_count == 11


@respx.mock
async def test_medrxiv_avisa_quando_bate_no_teto(caplog: pytest.LogCaptureFixture) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        cursor = int(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json=_pagina_medrxiv(cursor, 3010))

    respx.get(url__startswith=BASE_MEDRXIV).mock(side_effect=responder)
    async with MedRxiv() as medrxiv:
        papers = await medrxiv.periodo(dias=30, max_paginas=2)
    assert len(papers) == 200
    assert "3010" in caplog.text and "max_paginas" in caplog.text


def _esearch(ids: list[str], total: int) -> httpx.Response:
    return httpx.Response(200, json={"esearchresult": {"count": str(total), "idlist": ids}})


@respx.mock
async def test_pubmed_pagina_o_esearch_e_usa_edat() -> None:
    """count=285 com página de 200: antes o retmax=50 descartava o resto."""
    todos = [str(i) for i in range(285)]

    def responder(request: httpx.Request) -> httpx.Response:
        inicio = int(request.url.params["retstart"])
        tamanho = int(request.url.params["retmax"])
        assert request.url.params["datetype"] == "edat"
        return _esearch(todos[inicio : inicio + tamanho], len(todos))

    rota = respx.get(f"{BASE_PUBMED}/esearch.fcgi").mock(side_effect=responder)
    pubmed = PubMed()
    pubmed._limitador = LimitadorTaxa(0)
    async with pubmed:
        ids = await pubmed.buscar_ids("q", dias=7)
    assert ids == todos
    assert rota.call_count == 2


@respx.mock
async def test_pubmed_avisa_quando_bate_no_teto(caplog: pytest.LogCaptureFixture) -> None:
    todos = [str(i) for i in range(500)]

    def responder(request: httpx.Request) -> httpx.Response:
        inicio = int(request.url.params["retstart"])
        tamanho = int(request.url.params["retmax"])
        return _esearch(todos[inicio : inicio + tamanho], len(todos))

    respx.get(f"{BASE_PUBMED}/esearch.fcgi").mock(side_effect=responder)
    pubmed = PubMed()
    pubmed._limitador = LimitadorTaxa(0)
    async with pubmed:
        ids = await pubmed.buscar_ids("q", dias=7, max_ids=300)
    assert len(ids) == 300
    assert "500" in caplog.text and "max_ids" in caplog.text


@respx.mock
async def test_pubmed_efetch_em_lotes(xml_efetch: str) -> None:
    rota = respx.get(f"{BASE_PUBMED}/efetch.fcgi").mock(
        return_value=httpx.Response(200, text=xml_efetch)
    )
    pubmed = PubMed()
    pubmed._limitador = LimitadorTaxa(0)
    async with pubmed:
        papers = await pubmed.detalhes([str(i) for i in range(450)])
    assert rota.call_count == 3
    assert len(papers) == 3
