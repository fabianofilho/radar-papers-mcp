"""Sync: falha de fonte não pode passar em silêncio."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
import respx
from typer.testing import CliRunner

from radar_papers_mcp import cli
from radar_papers_mcp.config import Topico
from radar_papers_mcp.fetcher.medrxiv import BASE as BASE_MEDRXIV
from radar_papers_mcp.fetcher.pubmed import BASE as BASE_PUBMED
from radar_papers_mcp.fetcher.sync import ResultadoSync, sincronizar

TOPICOS = [Topico(nome="sepse", pubmed="sepsis", medrxiv=("sepsis",))]


@respx.mock
async def test_sync_sem_rede_registra_falha_de_cada_fonte(
    db: duckdb.DuckDBPyConnection,
) -> None:
    """Antes as exceções viravam warning e o resultado parecia um dia sem papers."""
    respx.get(url__startswith=BASE_PUBMED).mock(side_effect=httpx.ConnectError("sem rede"))
    respx.get(url__startswith=BASE_MEDRXIV).mock(side_effect=httpx.ConnectError("sem rede"))

    resultado = await sincronizar(db, TOPICOS, dias=7)

    assert resultado.novos == 0
    assert len(resultado.falhas) == 2
    assert resultado.falhas[0].startswith("pubmed/sepse:")
    assert resultado.falhas[1].startswith("medrxiv:")


@respx.mock
async def test_sync_sem_falha_devolve_falhas_vazio(db: duckdb.DuckDBPyConnection) -> None:
    respx.get(f"{BASE_PUBMED}/esearch.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"count": "0", "idlist": []}})
    )
    vazio: dict[str, Any] = {"messages": [{"status": "ok", "total": "0"}], "collection": []}
    respx.get(url__startswith=BASE_MEDRXIV).mock(return_value=httpx.Response(200, json=vazio))

    resultado = await sincronizar(db, TOPICOS, dias=7)

    assert resultado.falhas == ()


def _preparar_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, resultado: ResultadoSync
) -> None:
    topicos = tmp_path / "topicos.yaml"
    topicos.write_text("topicos:\n  - nome: sepse\n    pubmed: sepsis\n", encoding="utf-8")
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "cli.duckdb"))
    monkeypatch.setenv("TOPICOS_PATH", str(topicos))

    async def falso(*_: Any, **__: Any) -> ResultadoSync:
        return resultado

    monkeypatch.setattr(cli, "sincronizar", falso)


def test_cli_sync_sai_com_1_quando_uma_fonte_falha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Com saída 0 o systemd marcava SUCCESS e o OnFailure nunca disparava."""
    resultado = ResultadoSync(
        novos=0,
        ja_conhecidos=0,
        por_fonte={"pubmed": 0, "medrxiv": 0},
        falhas=("medrxiv: All connection attempts failed",),
    )
    _preparar_cli(monkeypatch, tmp_path, resultado)

    saida = CliRunner().invoke(cli.app, ["sync"])

    assert saida.exit_code == 1
    assert "medrxiv: All connection attempts failed" in saida.output


def test_cli_sync_sai_com_0_sem_falha(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    resultado = ResultadoSync(novos=3, ja_conhecidos=1, por_fonte={"pubmed": 4, "medrxiv": 0})
    _preparar_cli(monkeypatch, tmp_path, resultado)

    saida = CliRunner().invoke(cli.app, ["sync"])

    assert saida.exit_code == 0, saida.output
    assert "3 novos" in saida.output
