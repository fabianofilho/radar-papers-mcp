"""As duas tools: buscar papers novos e resumir um paper."""

from __future__ import annotations

import logging
import unicodedata
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
    topicos_na_base,
)

logger = logging.getLogger(__name__)

AVISO_BASE_VAZIA = (
    "A base local ainda não foi sincronizada. Rode 'papers-cli sync' antes de consultar."
)
AVISO_BASE_TRAVADA = (
    "A base local existe mas não pôde ser lida agora, provavelmente há um sync em "
    "andamento. Tente de novo em alguns minutos."
)
AVISO_ERRO_CONSULTA = "Falha inesperada ao consultar a base local ({erro}). Veja o log do servidor."
AVISO_RESUMO = (
    "O resumo é gerado por LLM local a partir do abstract, não do texto completo. "
    "Sempre confira no link original antes de citar."
)

# Teto da janela: a base guarda o que o sync coletou, e um valor enorme de
# 'dias' estourava a aritmética de data em vez de devolver um aviso útil.
DIAS_MAX = 365
LIMITE_PADRAO = 50
LIMITE_MAX = 200


class PaperEncontrado(BaseModel):
    """Um paper, sempre com o link original."""

    chave: str = Field(description="Identificador para usar em resumir_paper")
    doi: str | None = None
    fonte: str
    titulo: str
    autores: str | None = None
    veiculo: str | None = None
    data_publicacao: date | None = Field(
        default=None, description="Data da edição da revista (ou da postagem do preprint)"
    )
    data_entrada: date | None = Field(
        default=None, description="Data em que o paper entrou na fonte; é a que define 'novo'"
    )
    url: str
    topicos: list[str] = Field(default_factory=list)
    tem_abstract: bool


class RespostaBusca(BaseModel):
    topico: str | None
    dias: int
    total: int = Field(description="Quantos papers o período tem, mesmo além do limite")
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
        data_entrada=linha.get("data_entrada"),
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
    except Exception as erro:  # noqa: BLE001
        logger.exception("falha ao consultar a base")
        return None, AVISO_ERRO_CONSULTA.format(erro=type(erro).__name__)


def _normalizar(texto: str) -> str:
    """Caixa, acento e espaços não importam na comparação de tópicos."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return " ".join(sem_acento.casefold().split())


def resolver_topico(pedido: str, disponiveis: list[str]) -> list[str]:
    """Tópicos da base que correspondem ao pedido.

    Primeiro por igualdade normalizada; senão, os tópicos cujo nome contém todas
    as palavras do pedido como palavras inteiras ("fairness" acha "fairness em
    IA médica", mas "calibração" não acha "multicalibração"). Uma lista com mais
    de um item é ambígua.
    """
    alvo = _normalizar(pedido)
    exatos = [t for t in disponiveis if _normalizar(t) == alvo]
    if exatos:
        return exatos[:1]
    palavras = set(alvo.split())
    if not palavras:
        return []
    return [t for t in disponiveis if palavras <= set(_normalizar(t).split())]


async def buscar_papers_novos(
    topico: str | None = None,
    dias: int = 7,
    *,
    caminho_db: str,
    limite: int = LIMITE_PADRAO,
) -> RespostaBusca:
    """Papers que entraram na fonte no período, opcionalmente filtrados por tópico."""

    def vazia(aviso: str | None) -> RespostaBusca:
        return RespostaBusca(topico=topico, dias=dias, total=0, resultados=[], aviso=aviso)

    if dias <= 0 or dias > DIAS_MAX:
        return vazia(f"O parâmetro 'dias' precisa estar entre 1 e {DIAS_MAX}.")
    if limite <= 0 or limite > LIMITE_MAX:
        return vazia(f"O parâmetro 'limite' precisa estar entre 1 e {LIMITE_MAX}.")

    def consultar(conexao: Any) -> tuple[str | None, list[str], list[dict[str, Any]], int]:
        disponiveis = topicos_na_base(conexao)
        nome: str | None = None
        if topico:
            casados = resolver_topico(topico, disponiveis)
            if len(casados) != 1:
                return None, casados or disponiveis, [], 0
            nome = casados[0]
        linhas, total = papers_do_periodo(conexao, dias=dias, topico=nome, limite=limite)
        return nome, disponiveis, linhas, total

    resultado, aviso = _ler(caminho_db, consultar)
    if resultado is None:
        return vazia(aviso)

    nome, opcoes, linhas, total = resultado
    if topico and nome is None:
        if not opcoes:
            return vazia("A base ainda não tem papers de nenhum tópico.")
        return vazia(
            f"O tópico {topico!r} não corresponde a um único tópico da base. "
            f"Use um destes: {'; '.join(opcoes)}."
        )

    if not linhas:
        aviso = "Nenhum paper novo no período para esse tópico."
    elif total > len(linhas):
        aviso = (
            f"Mostrando os {len(linhas)} mais recentes de {total} papers do período. "
            "Reduza 'dias', filtre por tópico ou aumente 'limite' "
            f"(até {LIMITE_MAX})."
        )
    else:
        aviso = None
    return RespostaBusca(
        topico=nome,
        dias=dias,
        total=total,
        resultados=[_para_modelo(linha) for linha in linhas],
        aviso=aviso,
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
