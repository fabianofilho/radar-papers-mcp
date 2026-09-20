"""Modelo comum aos dois fetchers e o limitador de taxa."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Paper:
    """Um artigo, normalizado independente da fonte."""

    doi: str | None
    identificador: str
    fonte: str
    titulo: str
    autores: str | None
    veiculo: str | None
    data_publicacao: date | None
    abstract: str | None
    url: str

    @property
    def chave(self) -> str:
        """DOI quando existe; senão o id da fonte.

        A deduplicação é por DOI porque o mesmo paper aparece em buscas de
        tópicos diferentes — e um preprint do medRxiv pode depois sair no PubMed
        com o mesmo DOI.
        """
        return (self.doi or f"{self.fonte}:{self.identificador}").lower()


class LimitadorTaxa:
    """Espaça as requisições para respeitar o limite da API.

    A NCBI permite 3 requisições por segundo sem chave e 10 com chave. Passar
    disso devolve erro de rate limit, como acontece na prática.
    """

    def __init__(self, por_segundo: float) -> None:
        self._intervalo = 1.0 / por_segundo if por_segundo > 0 else 0.0
        self._ultima = 0.0

    async def esperar(self) -> None:
        if self._intervalo <= 0:
            return
        agora = time.monotonic()
        restante = self._intervalo - (agora - self._ultima)
        if restante > 0:
            await asyncio.sleep(restante)
        self._ultima = time.monotonic()
