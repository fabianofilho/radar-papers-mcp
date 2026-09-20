"""Tools: busca, resumo, cache e ausência de abstract."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
import respx

from radar_papers_mcp.fetcher.base import Paper
from radar_papers_mcp.llm.qwen_client import QwenClient
from radar_papers_mcp.llm.resumir import AbstractAusente, resumir
from radar_papers_mcp.mcp_server.tools.papers import buscar_papers_novos, resumir_paper
from radar_papers_mcp.store.db import conectar
from radar_papers_mcp.store.queries import gravar

ENDPOINT = "http://llm-de-teste/v1"
MODELO = "modelo-de-teste"

RESUMO_JSON = (
    '{"problema": "Modelos clínicos são mal calibrados.",'
    ' "metodo": "Coorte retrospectiva com 10 mil pacientes.",'
    ' "achado_principal": "Recalibração reduziu o erro em 30%.",'
    ' "relevancia": "Direto ao tópico de calibração."}'
)


def _paper(**campos: object) -> Paper:
    base = {
        "doi": "10.1101/abc",
        "identificador": "abc",
        "fonte": "medrxiv",
        "titulo": "Calibração de modelos clínicos",
        "autores": "Silva J",
        "veiculo": "medrxiv (preprint)",
        "data_publicacao": date.today() - timedelta(days=1),
        "abstract": "Avaliamos a calibração de modelos clínicos.",
        "url": "https://doi.org/10.1101/abc",
    }
    base.update(campos)
    return Paper(**base)  # type: ignore[arg-type]


def _chat(conteudo: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": conteudo}}]})


async def test_base_inexistente_avisa(caminho_db: str) -> None:
    resposta = await buscar_papers_novos(None, 7, caminho_db=caminho_db)
    assert resposta.total == 0
    assert resposta.aviso is not None and "sincronizada" in resposta.aviso


async def test_busca_devolve_chave_e_link(caminho_db: str) -> None:
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper()], "calibração")
    resposta = await buscar_papers_novos(None, 7, caminho_db=caminho_db)
    assert resposta.total == 1
    resultado = resposta.resultados[0]
    assert resultado.chave == "10.1101/abc"
    assert resultado.url.startswith("https://doi.org/")
    assert resultado.tem_abstract is True


async def test_dias_invalido(caminho_db: str) -> None:
    resposta = await buscar_papers_novos(None, 0, caminho_db=caminho_db)
    assert resposta.total == 0
    assert resposta.aviso is not None


# --- resumo ----------------------------------------------------------------


async def test_sem_abstract_nao_inventa_resumo() -> None:
    """Resumir um texto vazio produziria invenção plausível: melhor falhar."""
    async with QwenClient(ENDPOINT, MODELO) as cliente:
        with pytest.raises(AbstractAusente):
            await resumir(cliente, titulo="Um paper", abstract=None, topico="x")
        with pytest.raises(AbstractAusente):
            await resumir(cliente, titulo="Um paper", abstract="   ", topico="x")


@respx.mock
async def test_resumo_estruturado(caminho_db: str) -> None:
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper()], "calibração")
    respx.get(f"{ENDPOINT}/models").mock(return_value=httpx.Response(200, json={"data": []}))
    respx.post(f"{ENDPOINT}/chat/completions").mock(return_value=_chat(RESUMO_JSON))

    resposta = await resumir_paper(
        "10.1101/abc", caminho_db=caminho_db, qwen_endpoint=ENDPOINT, qwen_model=MODELO
    )
    assert resposta.origem == "llm"
    assert resposta.resumo is not None
    assert "30%" in resposta.resumo.achado_principal
    assert resposta.aviso is not None and "abstract" in resposta.aviso


@respx.mock
async def test_resumo_usa_cache_na_segunda_vez(caminho_db: str) -> None:
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper()], "calibração")
    respx.get(f"{ENDPOINT}/models").mock(return_value=httpx.Response(200, json={"data": []}))
    rota = respx.post(f"{ENDPOINT}/chat/completions").mock(return_value=_chat(RESUMO_JSON))

    kwargs = {"caminho_db": caminho_db, "qwen_endpoint": ENDPOINT, "qwen_model": MODELO}
    primeira = await resumir_paper("10.1101/abc", **kwargs)  # type: ignore[arg-type]
    segunda = await resumir_paper("10.1101/abc", **kwargs)  # type: ignore[arg-type]

    assert primeira.origem == "llm"
    assert segunda.origem == "cache"
    assert rota.call_count == 1


@respx.mock
async def test_paper_sem_abstract_avisa_com_link(caminho_db: str) -> None:
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper(abstract=None)], "calibração")
    respx.get(f"{ENDPOINT}/models").mock(return_value=httpx.Response(200, json={"data": []}))

    resposta = await resumir_paper(
        "10.1101/abc", caminho_db=caminho_db, qwen_endpoint=ENDPOINT, qwen_model=MODELO
    )
    assert resposta.resumo is None
    assert resposta.origem == "indisponivel"
    assert resposta.url.startswith("https://")


@respx.mock
async def test_llm_fora_do_ar_devolve_o_link(caminho_db: str) -> None:
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper()], "calibração")
    respx.get(f"{ENDPOINT}/models").mock(side_effect=httpx.ConnectError("recusado"))

    resposta = await resumir_paper(
        "10.1101/abc", caminho_db=caminho_db, qwen_endpoint=ENDPOINT, qwen_model=MODELO
    )
    assert resposta.resumo is None
    assert resposta.aviso is not None and "link original continua" in resposta.aviso


@respx.mock
async def test_resumo_incompleto_e_recusado(caminho_db: str) -> None:
    """Faltando campo, é melhor falhar do que devolver meio resumo."""
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper()], "calibração")
    respx.get(f"{ENDPOINT}/models").mock(return_value=httpx.Response(200, json={"data": []}))
    respx.post(f"{ENDPOINT}/chat/completions").mock(
        return_value=_chat('{"problema": "x", "metodo": "y"}')
    )
    resposta = await resumir_paper(
        "10.1101/abc", caminho_db=caminho_db, qwen_endpoint=ENDPOINT, qwen_model=MODELO
    )
    assert resposta.resumo is None
    assert resposta.origem == "indisponivel"


async def test_chave_inexistente(caminho_db: str) -> None:
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper()], "x")
    resposta = await resumir_paper(
        "nao-existe", caminho_db=caminho_db, qwen_endpoint=ENDPOINT, qwen_model=MODELO
    )
    assert resposta.aviso is not None and "buscar_papers_novos" in resposta.aviso
