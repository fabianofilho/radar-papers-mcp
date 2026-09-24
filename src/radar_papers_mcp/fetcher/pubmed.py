"""Cliente da API E-utilities do PubMed.

esearch devolve os IDs, efetch devolve o detalhe em XML. O rate limit da NCBI é
de 3 requisições por segundo sem chave e 10 com chave, e ele é aplicado de
verdade: passar disso devolve ``{"error": "API rate limit exceeded"}``.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any
from xml.etree import ElementTree

import httpx

from radar_papers_mcp.fetcher.base import LimitadorTaxa, Paper

logger = logging.getLogger(__name__)

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
FONTE = "pubmed"
USER_AGENT = "radar-papers-mcp/0.1 (+https://github.com/fabianofilho/radar-papers-mcp)"
# Tamanho da página do esearch e do lote do efetch.
POR_PAGINA = 200
# Teto de IDs por query e por sync. Acima disso a query está larga demais para
# um radar diário, e o sync avisa no log em vez de truncar em silêncio.
MAX_IDS = 1000

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


def _data_do_no(no: ElementTree.Element) -> date | None:
    ano = _texto(no.find("Year"))
    if not ano:
        return None
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
        return None


def _data(artigo: ElementTree.Element) -> date | None:
    """Data da edição da revista, só para exibição.

    Quando a revista informa só ano ou ano e mês, o dia e o mês faltantes viram 1.
    """
    for caminho in (
        "./MedlineCitation/Article/Journal/JournalIssue/PubDate",
        "./PubmedData/History/PubMedPubDate",
    ):
        no = artigo.find(caminho)
        if no is None:
            continue
        data = _data_do_no(no)
        if data is not None:
            return data
    return None


def _data_entrada(artigo: ElementTree.Element) -> date | None:
    """Data em que o paper entrou no PubMed (Entrez), a mesma do filtro ``edat``."""
    for status in ("entrez", "pubmed"):
        no = artigo.find(f"./PubmedData/History/PubMedPubDate[@PubStatus='{status}']")
        if no is None:
            continue
        data = _data_do_no(no)
        if data is not None:
            return data
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
                data_entrada=_data_entrada(artigo),
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

    async def _esearch(
        self, query: str, *, dias: int, retstart: int, retmax: int
    ) -> dict[str, Any]:
        await self._limitador.esperar()
        resposta = await self._exigir_client().get(
            f"{BASE}/esearch.fcgi",
            params=self._params(
                {
                    "term": query,
                    "retmode": "json",
                    "retstart": str(retstart),
                    "retmax": str(retmax),
                    # edat: data de entrada no PubMed (Entrez). É a mesma data que a
                    # busca usa para decidir o que é novo; a data da edição (pdat)
                    # pode estar meses antes ou depois.
                    "datetype": "edat",
                    "reldate": str(dias),
                    "sort": "date",
                }
            ),
        )
        resposta.raise_for_status()
        corpo = resposta.json()
        if "esearchresult" not in corpo:
            raise PubMedIndisponivel(f"esearch devolveu algo inesperado: {str(corpo)[:120]}")
        return dict(corpo["esearchresult"])

    async def buscar_ids(self, query: str, *, dias: int, max_ids: int = MAX_IDS) -> list[str]:
        """IDs dos papers que entraram no PubMed no período e casam com a query.

        Pagina até o ``count`` do esearch. Se passar de ``max_ids``, para ali e
        registra um aviso no log.
        """
        ids: list[str] = []
        while True:
            resultado = await self._esearch(
                query,
                dias=dias,
                retstart=len(ids),
                retmax=min(POR_PAGINA, max_ids - len(ids)),
            )
            pagina = [str(i) for i in resultado.get("idlist", [])]
            ids.extend(pagina)
            total = int(resultado.get("count", 0) or 0)
            if not pagina or len(ids) >= total:
                break
            if len(ids) >= max_ids:
                logger.warning(
                    "PubMed: a query %r tem %d papers no período; coletados só %d (max_ids). "
                    "Estreite a query ou reduza a janela.",
                    query,
                    total,
                    len(ids),
                )
                break
        return ids

    async def detalhes(self, ids: list[str]) -> list[Paper]:
        """Detalhe dos papers, em lotes de ``POR_PAGINA`` por efetch."""
        papers: list[Paper] = []
        for inicio in range(0, len(ids), POR_PAGINA):
            lote = ids[inicio : inicio + POR_PAGINA]
            await self._limitador.esperar()
            resposta = await self._exigir_client().get(
                f"{BASE}/efetch.fcgi",
                params=self._params({"id": ",".join(lote), "retmode": "xml"}),
            )
            resposta.raise_for_status()
            papers.extend(parse_efetch(resposta.text))
        return papers

    async def buscar(self, query: str, *, dias: int, max_ids: int = MAX_IDS) -> list[Paper]:
        return await self.detalhes(await self.buscar_ids(query, dias=dias, max_ids=max_ids))

    def _exigir_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("PubMed precisa ser usado como 'async with'")
        return self._client
