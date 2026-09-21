"""Cliente da API E-utilities do PubMed.

esearch devolve os IDs, efetch devolve o detalhe em XML. O rate limit da NCBI é
de 3 requisições por segundo sem chave e 10 com chave — e ele é aplicado de
verdade: passar disso devolve ``{"error": "API rate limit exceeded"}``.
"""

from __future__ import annotations

import logging
from datetime import date
from xml.etree import ElementTree

import httpx

from radar_papers_mcp.fetcher.base import LimitadorTaxa, Paper

logger = logging.getLogger(__name__)

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
FONTE = "pubmed"
USER_AGENT = "radar-papers-mcp/0.1 (+https://github.com/fabianofilho/radar-papers-mcp)"

_MESES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


class PubMedIndisponivel(RuntimeError):
    """A API não respondeu como esperado."""


def _texto(elemento: ElementTree.Element | None) -> str | None:
    if elemento is None:
        return None
    texto = "".join(elemento.itertext()).strip()
    return texto or None


def _data(artigo: ElementTree.Element) -> date | None:
    for caminho in (
        "./MedlineCitation/Article/Journal/JournalIssue/PubDate",
        "./PubmedData/History/PubMedPubDate",
    ):
        no = artigo.find(caminho)
        if no is None:
            continue
        ano = _texto(no.find("Year"))
        if not ano:
            continue
        mes_bruto = (_texto(no.find("Month")) or "1").lower()
        mes = _MESES.get(mes_bruto[:3], None)
        if mes is None:
            try:
                mes = int(mes_bruto)
            except ValueError:
                mes = 1
        dia = _texto(no.find("Day")) or "1"
        try:
            return date(int(ano), mes, int(dia))
        except ValueError:
            continue
    return None


def parse_efetch(xml: str) -> list[Paper]:
    """Converte o XML do efetch em papers normalizados."""
    try:
        raiz = ElementTree.fromstring(xml)
    except ElementTree.ParseError as erro:
        raise PubMedIndisponivel("resposta do efetch não era XML válido") from erro

    papers: list[Paper] = []
    for artigo in raiz.findall(".//PubmedArticle"):
        pmid = _texto(artigo.find("./MedlineCitation/PMID"))
        titulo = _texto(artigo.find(".//ArticleTitle"))
        if not pmid or not titulo:
            continue

        doi = None
        for identificador in artigo.findall(".//ArticleId"):
            if identificador.get("IdType") == "doi":
                doi = _texto(identificador)
                break

        autores = [
            f"{_texto(a.find('LastName')) or ''} {_texto(a.find('Initials')) or ''}".strip()
            for a in artigo.findall(".//Author")
        ]
        abstract_partes = [
            _texto(parte) or "" for parte in artigo.findall(".//Abstract/AbstractText")
        ]

        papers.append(
            Paper(
                doi=doi,
                identificador=pmid,
                fonte=FONTE,
                titulo=titulo,
                autores=", ".join(a for a in autores if a) or None,
                veiculo=_texto(artigo.find(".//Journal/Title")),
                data_publicacao=_data(artigo),
                abstract=" ".join(p for p in abstract_partes if p) or None,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            )
        )
    return papers


class PubMed:
    """esearch + efetch, respeitando o rate limit."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key or None
        self._limitador = LimitadorTaxa(10.0 if self._api_key else 3.0)
        self._client = client
        self._client_proprio = client is None

    async def __aenter__(self) -> PubMed:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0, headers={"User-Agent": USER_AGENT})
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client is not None and self._client_proprio:
            await self._client.aclose()
            self._client = None

    def _params(self, extra: dict[str, str]) -> dict[str, str]:
        params = {"db": "pubmed", **extra}
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    async def buscar_ids(self, query: str, *, dias: int, retmax: int = 50) -> list[str]:
        """IDs dos papers do período que casam com a query."""
        await self._limitador.esperar()
        resposta = await self._exigir_client().get(
            f"{BASE}/esearch.fcgi",
            params=self._params(
                {
                    "term": query,
                    "retmode": "json",
                    "retmax": str(retmax),
                    "datetype": "pdat",
                    "reldate": str(dias),
                    "sort": "date",
                }
            ),
        )
        resposta.raise_for_status()
        corpo = resposta.json()
        if "esearchresult" not in corpo:
            raise PubMedIndisponivel(f"esearch devolveu algo inesperado: {str(corpo)[:120]}")
        return list(corpo["esearchresult"].get("idlist", []))

    async def detalhes(self, ids: list[str]) -> list[Paper]:
        """Detalhe dos papers, em um único efetch."""
        if not ids:
            return []
        await self._limitador.esperar()
        resposta = await self._exigir_client().get(
            f"{BASE}/efetch.fcgi",
            params=self._params({"id": ",".join(ids), "retmode": "xml"}),
        )
        resposta.raise_for_status()
        return parse_efetch(resposta.text)

    async def buscar(self, query: str, *, dias: int, retmax: int = 50) -> list[Paper]:
        return await self.detalhes(await self.buscar_ids(query, dias=dias, retmax=retmax))

    def _exigir_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("PubMed precisa ser usado como 'async with'")
        return self._client
