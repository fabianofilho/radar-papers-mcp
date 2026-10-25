"""Entrypoint MCP do radar de papers: stdio por padrão, HTTP no modo connector."""

from __future__ import annotations

import base64
import logging
import sys
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import Icon

from radar_papers_mcp.config import TopicosInvalidos, carregar_config, carregar_topicos
from radar_papers_mcp.mcp_server.capacidades import esconder_o_que_nao_existe
from radar_papers_mcp.mcp_server.limite import LimitadorPorOrigem, origem_da_requisicao
from radar_papers_mcp.mcp_server.tools.papers import RespostaBusca, RespostaResumo
from radar_papers_mcp.mcp_server.tools.papers import buscar_papers_novos as _buscar_papers_novos
from radar_papers_mcp.mcp_server.tools.papers import resumir_paper as _resumir_paper

logger = logging.getLogger(__name__)

# Ícone do servidor e das tools, no molde da família (ecg-mcp): o robô do IA.med
# com página com o sinal do radar no visor. Em data URI, e não URL: o cliente precisa ver o
# ícone antes de autenticar, e o endereço do Funnel é um só para todos os servidores.
ICONE = [
Icon(
    src="data:image/svg+xml;base64,"
    + base64.b64encode((Path(__file__).parent / "icone.svg").read_bytes()).decode(),
    mimeType="image/svg+xml",
    sizes=["any"],
)
]

mcp = MCPServer("radar-papers-mcp", version="0.1.0", icons=ICONE)


@mcp.tool(icons=ICONE)
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


@mcp.tool(icons=ICONE)
async def resumir_paper(paper_id: str) -> RespostaResumo:
    """Resumo estruturado de um paper: problema, método, achado e relevância.

    O resumo vem do abstract, não do texto completo, e é gerado pelo LLM local.
    Fica cacheado: o mesmo paper não é resumido duas vezes. Sem abstract, ou com
    o LLM fora do ar, resumo vem nulo, origem vem "indisponivel" e o aviso diz o
    motivo, em vez de um resumo inventado.

    No modo connector (servidor HTTP hospedado) o LLM não é chamado: só vem o
    resumo que já estiver em cache. Sem cache, resumo vem nulo, origem vem
    "indisponivel" e o aviso diz que não há resumo; abra o link original.

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
        somente_cache=config.modo_connector,
    )


def _seguranca_de_transporte(config: Any) -> Any:
    """Regras de Host/Origin quando o servidor atende por um nome público.

    O SDK só liga a proteção contra DNS rebinding sozinho quando o bind é
    loopback, e aí aceita apenas Host de loopback. Atrás de um túnel ou proxy o
    Host que chega é o nome público, e a requisição legítima levaria 421. Em vez
    de desligar a checagem, declaramos os nomes por onde o servidor responde.

    Devolve None quando não há nome público configurado, deixando o padrão do
    SDK valer.
    """
    if not config.http_hosts_publicos:
        return None

    from mcp.server.transport_security import TransportSecuritySettings

    hosts: list[str] = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    origens: list[str] = ["http://127.0.0.1:*", "http://localhost:*"]
    for nome in config.http_hosts_publicos:
        # Com e sem porta: atrás de TLS o Host costuma vir sem o ":443".
        hosts += [nome, f"{nome}:*"]
        origens += [f"https://{nome}", f"https://{nome}:*"]
    return TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origens)


def _com_limite(app: Any, limite_por_minuto: int, limite_global: int) -> Any:
    """Embrulha o app ASGI com o teto de requisições por origem.

    O `run()` do SDK não aceita middleware, então o app é construído por
    `streamable_http_app()`, embrulhado aqui e servido por uvicorn.
    """
    limitador = LimitadorPorOrigem(limite_por_minuto, limite_global)

    async def middleware(scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await app(scope, receive, send)
            return
        origem = origem_da_requisicao(scope)
        if not limitador.permitir(origem):
            logger.warning("limite %s excedido (origem %s)", limitador.motivo_ultima_recusa, origem)
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", b"60"),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"erro":"limite de requisicoes excedido; tente em 1 minuto"}',
                }
            )
            return
        await app(scope, receive, send)

    return middleware


def main() -> None:
    """Sobe o servidor MCP. Stdio por padrão; HTTP no modo connector."""
    config = carregar_config()
    # Este servidor só tem tools. Anunciar prompts e resources faria quem
    # mapeia o servidor gastar chamadas para descobrir lista vazia.
    esconder_o_que_nao_existe(mcp)
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if config.transporte == "stdio":
        logger.info("radar-papers-mcp subindo em stdio (base em %s)", config.duckdb_path)
        mcp.run(transport="stdio")
        return

    # Modo connector. A base é aberta somente para leitura a cada requisição, o
    # LLM não é chamado e nada é gravado: resumir_paper serve só o cache. O sync
    # roda FORA deste processo (papers-cli sync, pelo timer).
    import uvicorn

    app = _com_limite(
        mcp.streamable_http_app(
            streamable_http_path=config.http_path,
            stateless_http=config.http_stateless,
            host=config.http_host,
            transport_security=_seguranca_de_transporte(config),
        ),
        config.http_limite_por_minuto,
        config.http_limite_global_por_minuto,
    )
    logger.info(
        "radar-papers-mcp em http://%s:%d%s (stateless=%s, limite %d/min por origem e "
        "%d/min global, base=%s, só leitura, sem LLM)",
        config.http_host,
        config.http_porta,
        config.http_path,
        config.http_stateless,
        config.http_limite_por_minuto,
        config.http_limite_global_por_minuto,
        config.duckdb_path,
    )
    uvicorn.run(app, host=config.http_host, port=config.http_porta, log_level="warning")


if __name__ == "__main__":
    main()
