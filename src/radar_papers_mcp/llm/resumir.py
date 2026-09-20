"""Resumo estruturado do abstract, com nota de relevância ao tópico."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel

from radar_papers_mcp.llm.qwen_client import QwenClient, QwenIndisponivel

logger = logging.getLogger(__name__)

NOME_TEMPLATE = "resumir_paper.jinja2"


class AbstractAusente(ValueError):
    """Sem abstract não há o que resumir — e inventar seria pior."""


class ResumoPaper(BaseModel):
    """Resumo estruturado de um paper."""

    problema: str
    metodo: str
    achado_principal: str
    relevancia: str


def diretorio_prompts() -> Path:
    return Path(__file__).resolve().parents[3] / "prompts"


@lru_cache(maxsize=1)
def _ambiente() -> Environment:
    return Environment(
        loader=FileSystemLoader(diretorio_prompts()),
        autoescape=select_autoescape(default=False, default_for_string=False),
    )


def renderizar_prompt() -> str:
    return _ambiente().get_template(NOME_TEMPLATE).render()


async def resumir(
    cliente: QwenClient,
    *,
    titulo: str,
    abstract: str | None,
    topico: str,
) -> ResumoPaper:
    """Resumo estruturado a partir do abstract.

    Raises:
        AbstractAusente: quando não há abstract. Resumir um texto vazio produziria
            um resumo inventado, que é o pior resultado possível aqui.
        QwenIndisponivel: quando o modelo local não responde ou foge do schema.
    """
    if not abstract or not abstract.strip():
        raise AbstractAusente(
            f"o paper {titulo[:60]!r} não tem abstract disponível; não há o que resumir"
        )

    bruto = await cliente.pedir_json(
        renderizar_prompt(),
        f"Tópico monitorado: {topico}\n\nTítulo: {titulo}\n\nAbstract:\n{abstract}",
    )
    faltando = [
        campo
        for campo in ("problema", "metodo", "achado_principal", "relevancia")
        if not str(bruto.get(campo, "")).strip()
    ]
    if faltando:
        raise QwenIndisponivel(f"resumo veio sem os campos {faltando}")

    return ResumoPaper(
        problema=str(bruto["problema"]).strip(),
        metodo=str(bruto["metodo"]).strip(),
        achado_principal=str(bruto["achado_principal"]).strip(),
        relevancia=str(bruto["relevancia"]).strip(),
    )
