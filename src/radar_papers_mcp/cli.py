"""CLI de administração: busca manual e teste das tools."""

from __future__ import annotations

import asyncio
import json
import logging

import typer

from radar_papers_mcp.config import carregar_config, carregar_topicos
from radar_papers_mcp.fetcher.sync import sincronizar
from radar_papers_mcp.llm.qwen_client import QwenClient
from radar_papers_mcp.mcp_server.tools.papers import buscar_papers_novos, resumir_paper
from radar_papers_mcp.store.db import conectar

app = typer.Typer(help="Administração do radar-papers-mcp", no_args_is_help=True)


@app.command()
def topicos() -> None:
    """Mostra os tópicos monitorados."""
    config = carregar_config()
    for topico in carregar_topicos(config.topicos_path):
        typer.echo(f"{topico.nome}")
        typer.echo(f"  pubmed:  {topico.pubmed or '(não configurado)'}")
        typer.echo(f"  medrxiv: {', '.join(topico.medrxiv) or '(não configurado)'}")


@app.command()
def sync(
    dias: int = typer.Option(7, help="Janela de busca"),
    sem_medrxiv: bool = typer.Option(False, help="Só PubMed"),
) -> None:
    """Busca papers novos e grava na base local."""
    config = carregar_config()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )

    async def rodar() -> None:
        lista = carregar_topicos(config.topicos_path)
        with conectar(config.duckdb_path) as conexao:
            resultado = await sincronizar(
                conexao,
                lista,
                dias=dias,
                pubmed_api_key=config.pubmed_api_key or None,
                incluir_medrxiv=not sem_medrxiv,
            )
        typer.echo(
            f"{resultado.novos} novos, {resultado.ja_conhecidos} já conhecidos "
            f"(pubmed: {resultado.por_fonte['pubmed']}, medrxiv: {resultado.por_fonte['medrxiv']})"
        )

    asyncio.run(rodar())


@app.command()
def buscar(topico: str = typer.Argument(default=""), dias: int = 7) -> None:
    """Testa a busca fora do MCP."""
    config = carregar_config()
    resposta = asyncio.run(
        buscar_papers_novos(topico or None, dias, caminho_db=str(config.duckdb_path))
    )
    typer.echo(resposta.model_dump_json(indent=2))


@app.command()
def resumir(paper_id: str) -> None:
    """Testa o resumo fora do MCP."""
    config = carregar_config()
    resposta = asyncio.run(
        resumir_paper(
            paper_id,
            caminho_db=str(config.duckdb_path),
            qwen_endpoint=config.qwen_endpoint,
            qwen_model=config.qwen_model,
            timeout_segundos=config.qwen_timeout_segundos,
        )
    )
    typer.echo(resposta.model_dump_json(indent=2))


@app.command()
def llm() -> None:
    """Verifica se o LLM local responde."""
    config = carregar_config()

    async def checar() -> None:
        async with QwenClient(config.qwen_endpoint, config.qwen_model) as cliente:
            vivo = await cliente.esta_vivo()
        cor = typer.colors.GREEN if vivo else typer.colors.RED
        typer.secho(f"LLM local {'respondendo' if vivo else 'fora do ar'}", fg=cor)

    asyncio.run(checar())


@app.command()
def schema() -> None:
    """Contagens da base local."""
    config = carregar_config()
    with conectar(config.duckdb_path) as conexao:
        papers = conexao.execute("SELECT count(*) FROM papers").fetchone()
        resumos = conexao.execute("SELECT count(*) FROM resumos").fetchone()
        por_fonte = conexao.execute("SELECT fonte, count(*) FROM papers GROUP BY 1").fetchall()
    typer.echo(
        json.dumps(
            {
                "papers": int(papers[0]) if papers else 0,
                "resumos_em_cache": int(resumos[0]) if resumos else 0,
                "por_fonte": {f: int(n) for f, n in por_fonte},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
