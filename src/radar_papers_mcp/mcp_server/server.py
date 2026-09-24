"""Entrypoint MCP (stdio) do radar de papers."""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from radar_papers_mcp.config import TopicosInvalidos, carregar_config, carregar_topicos
from radar_papers_mcp.mcp_server.tools.papers import RespostaBusca, RespostaResumo
from radar_papers_mcp.mcp_server.tools.papers import buscar_papers_novos as _buscar_papers_novos
from radar_papers_mcp.mcp_server.tools.papers import resumir_paper as _resumir_paper

logger = logging.getLogger(__name__)

mcp = MCPServer("radar-papers-mcp", version="0.1.0")


@mcp.tool()
async def buscar_papers_novos(topico: str = "", dias: int = 7, limite: int = 50) -> RespostaBusca:
    """Papers que entraram no PubMed ou no medRxiv nos últimos dias, por tópico monitorado.

    Consulta a base local, alimentada pelo sync (papers-cli sync, em geral
    agendado por um timer diário). "Novo" é pela data de entrada na fonte
    (Entrez no PubMed, postagem no medRxiv), não pela data da edição da revista.
    Cada resultado traz a chave para usar em resumir_paper e o link original.
    'total' conta todos os papers do período; se passar de 'limite', o aviso diz.

    Args:
        topico: nome de um tópico configurado; vazio devolve todos. Caixa e acento
            não importam, e basta um trecho com palavras inteiras do nome
            ("fairness"). Se não corresponder a um único tópico, a resposta traz
            a lista dos tópicos válidos no aviso.
        dias: tamanho da janela, de 1 a 365, a contar de hoje.
        limite: máximo de resultados devolvidos, de 1 a 200. Os mais recentes vêm primeiro.
    """
    config = carregar_config()
    try:
        configurados = [t.nome for t in carregar_topicos(config.topicos_path)]
    except TopicosInvalidos as erro:
        logger.warning("tópicos não carregados: %s", erro)
        configurados = []
    return await _buscar_papers_novos(
        topico or None,
        dias,
        caminho_db=str(config.duckdb_path),
        limite=limite,
        topicos_configurados=configurados,
    )


@mcp.tool()
async def resumir_paper(paper_id: str) -> RespostaResumo:
    """Resumo estruturado de um paper: problema, método, achado e relevância.

    O resumo vem do abstract, não do texto completo, e é gerado pelo LLM local.
    Fica cacheado: o mesmo paper não é resumido duas vezes. Sem abstract, ou com
    o LLM fora do ar, resumo vem nulo, origem vem "indisponivel" e o aviso diz o
    motivo, em vez de um resumo inventado.

    Args:
        paper_id: a chave devolvida por buscar_papers_novos (o DOI, em geral).
    """
    config = carregar_config()
    return await _resumir_paper(
        paper_id,
        caminho_db=str(config.duckdb_path),
        qwen_endpoint=config.qwen_endpoint,
        qwen_model=config.qwen_model,
        timeout_segundos=config.qwen_timeout_segundos,
    )


def main() -> None:
    """Sobe o servidor MCP no stdio."""
    config = carregar_config()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("radar-papers-mcp subindo (base em %s)", config.duckdb_path)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
