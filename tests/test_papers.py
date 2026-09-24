"""Tools: busca, resumo, cache e ausência de abstract."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
import respx

from radar_papers_mcp.fetcher.base import Paper
from radar_papers_mcp.llm.qwen_client import QwenClient
from radar_papers_mcp.llm.resumir import AbstractAusente, renderizar_prompt, resumir
from radar_papers_mcp.mcp_server.tools.papers import (
    AVISO_BASE_TRAVADA,
    buscar_papers_novos,
    resolver_topico,
    resumir_paper,
)
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


# --- L3: limites, truncamento e erros ------------------------------------------


async def test_dias_acima_do_teto_avisa_sem_culpar_o_sync(caminho_db: str) -> None:
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper()], "calibração")
    resposta = await buscar_papers_novos(None, 100_000_000, caminho_db=caminho_db)
    assert resposta.total == 0
    assert resposta.aviso is not None and "365" in resposta.aviso
    assert resposta.aviso != AVISO_BASE_TRAVADA


async def test_limite_invalido(caminho_db: str) -> None:
    resposta = await buscar_papers_novos(None, 7, caminho_db=caminho_db, limite=0)
    assert resposta.aviso is not None and "limite" in resposta.aviso


async def test_truncamento_e_avisado_e_total_conta_tudo(caminho_db: str) -> None:
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper(doi=f"10.1/{i}") for i in range(7)], "calibração")
    resposta = await buscar_papers_novos(None, 7, caminho_db=caminho_db, limite=5)
    assert len(resposta.resultados) == 5
    assert resposta.total == 7
    assert resposta.aviso is not None and "5 mais recentes de 7" in resposta.aviso


async def test_erro_inesperado_nao_vira_aviso_de_sync(
    caminho_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from radar_papers_mcp.mcp_server.tools import papers as modulo

    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper()], "calibração")

    def quebra(*_a: object, **_k: object) -> None:
        raise OverflowError("date value out of range")

    monkeypatch.setattr(modulo, "papers_do_periodo", quebra)
    resposta = await buscar_papers_novos(None, 7, caminho_db=caminho_db)
    assert resposta.aviso is not None
    assert "OverflowError" in resposta.aviso
    assert "sync" not in resposta.aviso


# --- N2: tópico sem acento, sem caixa, com lista dos válidos --------------------

TOPICOS = ["calibração de modelos clínicos", "fairness em IA médica", "multicalibração"]


def test_resolver_topico() -> None:
    assert resolver_topico("Fairness", TOPICOS) == ["fairness em IA médica"]
    assert resolver_topico("calibracao", TOPICOS) == ["calibração de modelos clínicos"]
    assert resolver_topico("MULTICALIBRAÇÃO", TOPICOS) == ["multicalibração"]
    assert resolver_topico("calibration", TOPICOS) == []
    assert resolver_topico("   ", TOPICOS) == []


async def test_busca_por_topico_normalizado(caminho_db: str) -> None:
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper()], "calibração de modelos clínicos")
        gravar(conexao, [_paper(doi="10.1/multi")], "multicalibração")
    resposta = await buscar_papers_novos("Calibracao", 7, caminho_db=caminho_db)
    assert resposta.topico == "calibração de modelos clínicos"
    assert [r.chave for r in resposta.resultados] == ["10.1101/abc"]


async def test_topico_desconhecido_lista_os_validos(caminho_db: str) -> None:
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper()], "calibração de modelos clínicos")
        gravar(conexao, [_paper(doi="10.1/f")], "fairness em IA médica")
    resposta = await buscar_papers_novos("calibration", 7, caminho_db=caminho_db)
    assert resposta.total == 0
    assert resposta.aviso is not None
    assert "calibração de modelos clínicos" in resposta.aviso
    assert "fairness em IA médica" in resposta.aviso


# --- resumo ----------------------------------------------------------------


def test_prompt_vem_de_dentro_do_pacote() -> None:
    """O template é lido via importlib.resources, não de um caminho do checkout."""
    assert "SOMENTE com JSON" in renderizar_prompt()


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
