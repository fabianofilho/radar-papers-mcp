"""As duas tools: buscar papers novos e resumir um paper."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from radar_papers_mcp.llm.qwen_client import QwenClient, QwenIndisponivel
from radar_papers_mcp.llm.resumir import AbstractAusente, ResumoPaper, resumir
from radar_papers_mcp.store.db import BaseIndisponivel, conectar
from radar_papers_mcp.store.queries import (
    gravar_resumo,
    papers_do_periodo,
    por_chave,
    resumo_em_cache,
)

logger = logging.getLogger(__name__)

AVISO_BASE_VAZIA = (
    "A base local ainda não foi sincronizada. Rode 'papers-cli sync' antes de consultar."
)
AVISO_BASE_TRAVADA = (
    "A base local existe mas não pôde ser lida agora — provavelmente há um sync em "
    "andamento. Tente de novo em alguns minutos."
)
AVISO_RESUMO = (
    "O resumo é gerado por LLM local a partir do abstract, não do texto completo. "
    "Sempre confira no link original antes de citar."
)


class PaperEncontrado(BaseModel):
    """Um paper, sempre com o link original."""

    chave: str = Field(description="Identificador para usar em resumir_paper")
    doi: str | None = None
    fonte: str
    titulo: str
    autores: str | None = None
    veiculo: str | None = None
    data_publicacao: date | None = None
    url: str
    topicos: list[str] = Field(default_factory=list)
    tem_abstract: bool


class RespostaBusca(BaseModel):
    topico: str | None
    dias: int
    total: int
    resultados: list[PaperEncontrado]
    aviso: str | None = None


class RespostaResumo(BaseModel):
    chave: str
    titulo: str
    url: str
    resumo: ResumoPaper | None = None
    origem: str = Field(description="llm, cache ou indisponivel")
    aviso: str | None = None


def _para_modelo(linha: dict[str, Any]) -> PaperEncontrado:
    return PaperEncontrado(
        chave=linha["chave"],
        doi=linha.get("doi"),
        fonte=linha["fonte"],
        titulo=linha["titulo"],
        autores=linha.get("autores"),
        veiculo=linha.get("veiculo"),
        data_publicacao=linha.get("data_publicacao"),
        url=linha["url"],
        topicos=[t for t in (linha.get("topicos") or "").split("|") if t],
        tem_abstract=bool(linha.get("abstract")),
    )


def _ler(caminho_db: str, funcao: Any) -> tuple[Any, str | None]:
    try:
        with conectar(caminho_db, somente_leitura=True) as conexao:
            return funcao(conexao), None
    except FileNotFoundError:
        return None, AVISO_BASE_VAZIA
    except BaseIndisponivel as erro:
        logger.warning("base indisponível: %s", erro)
        return None, AVISO_BASE_TRAVADA
    except Exception:  # noqa: BLE001
        logger.exception("falha ao consultar a base")
        return None, AVISO_BASE_TRAVADA


async def buscar_papers_novos(
    topico: str | None = None,
    dias: int = 7,
    *,
    caminho_db: str,
    limite: int = 50,
) -> RespostaBusca:
    """Papers do período, opcionalmente filtrados por tópico."""
    if dias <= 0:
        return RespostaBusca(
            topico=topico,
            dias=dias,
            total=0,
            resultados=[],
            aviso="O parâmetro 'dias' precisa ser maior que zero.",
        )

    linhas, aviso = _ler(
        caminho_db,
        lambda c: papers_do_periodo(c, dias=dias, topico=topico or None, limite=limite),
    )
    if linhas is None:
        return RespostaBusca(topico=topico, dias=dias, total=0, resultados=[], aviso=aviso)

    return RespostaBusca(
        topico=topico,
        dias=dias,
        total=len(linhas),
        resultados=[_para_modelo(linha) for linha in linhas],
        aviso=None if linhas else "Nenhum paper novo no período para esse tópico.",
    )


async def resumir_paper(
    paper_id: str,
    *,
    caminho_db: str,
    qwen_endpoint: str,
    qwen_model: str,
    timeout_segundos: float = 180.0,
) -> RespostaResumo:
    """Resumo estruturado de um paper, com cache."""
    linha, aviso = _ler(caminho_db, lambda c: por_chave(c, paper_id))
    if aviso:
        return RespostaResumo(chave=paper_id, titulo="", url="", origem="indisponivel", aviso=aviso)
    if linha is None:
        return RespostaResumo(
            chave=paper_id,
            titulo="",
            url="",
            origem="indisponivel",
            aviso=(
                f"Nenhum paper com a chave {paper_id!r}. "
                "Use buscar_papers_novos para achar a chave correta."
            ),
        )

    cacheado, _ = _ler(caminho_db, lambda c: resumo_em_cache(c, paper_id))
    if cacheado:
        return RespostaResumo(
            chave=linha["chave"],
            titulo=linha["titulo"],
            url=linha["url"],
            resumo=ResumoPaper(
                problema=cacheado["problema"],
                metodo=cacheado["metodo"],
                achado_principal=cacheado["achado_principal"],
                relevancia=cacheado["relevancia"],
            ),
            origem="cache",
            aviso=AVISO_RESUMO,
        )

    topicos = [t for t in (linha.get("topicos") or "").split("|") if t]
    try:
        async with QwenClient(
            qwen_endpoint, qwen_model, timeout_segundos=timeout_segundos
        ) as cliente:
            if not await cliente.esta_vivo():
                raise QwenIndisponivel("LLM local não respondeu")
            resultado = await resumir(
                cliente,
                titulo=linha["titulo"],
                abstract=linha.get("abstract"),
                topico=topicos[0] if topicos else "IA aplicada à saúde",
            )
    except AbstractAusente as erro:
        return RespostaResumo(
            chave=linha["chave"],
            titulo=linha["titulo"],
            url=linha["url"],
            origem="indisponivel",
            aviso=f"{erro}. Abra o link original.",
        )
    except QwenIndisponivel as erro:
        return RespostaResumo(
            chave=linha["chave"],
            titulo=linha["titulo"],
            url=linha["url"],
            origem="indisponivel",
            aviso=f"Não foi possível resumir: {erro}. O link original continua acessível.",
        )

    try:
        with conectar(caminho_db) as conexao:
            gravar_resumo(
                conexao,
                chave=linha["chave"],
                topico=topicos[0] if topicos else "",
                problema=resultado.problema,
                metodo=resultado.metodo,
                achado_principal=resultado.achado_principal,
                relevancia=resultado.relevancia,
                modelo=qwen_model,
            )
    except Exception:  # noqa: BLE001 - cache é otimização, não resposta
        logger.warning("resumo não foi cacheado")

    return RespostaResumo(
        chave=linha["chave"],
        titulo=linha["titulo"],
        url=linha["url"],
        resumo=resultado,
        origem="llm",
        aviso=AVISO_RESUMO,
    )
