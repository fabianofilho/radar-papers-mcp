"""Entrypoint MCP (stdio) do radar de papers."""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from radar_papers_mcp.config import carregar_config
from radar_papers_mcp.mcp_server.tools.papers import RespostaBusca, RespostaResumo
from radar_papers_mcp.mcp_server.tools.papers import buscar_papers_novos as _buscar_papers_novos
from radar_papers_mcp.mcp_server.tools.papers import resumir_paper as _resumir_paper

logger = logging.getLogger(__name__)

mcp = MCPServer("radar-papers-mcp", version="0.1.0")


@mcp.tool()
async def buscar_papers_novos(topico: str = "", dias: int = 7) -> RespostaBusca:
    """Papers novos do PubMed e do medRxiv sobre os tópicos monitorados.

    Consulta a base local, que é sincronizada de madrugada. Cada resultado traz
    a chave para usar em resumir_paper e o link original.

    Args:
        topico: filtra por um tópico configurado; vazio devolve todos.
        dias: tamanho da janela, em dias, a contar de hoje.
    """
    config = carregar_config()
    return await _buscar_papers_novos(topico or None, dias, caminho_db=str(config.duckdb_path))


@mcp.tool()
async def resumir_paper(paper_id: str) -> RespostaResumo:
    """Resumo estruturado de um paper: problema, método, achado e relevância.

    O resumo vem do abstract, não do texto completo, e é gerado pelo LLM local.
    Fica cacheado: o mesmo paper não é resumido duas vezes. Papers sem abstract
    devolvem erro claro em vez de um resumo inventado.

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
