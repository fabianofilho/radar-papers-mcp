"""Modo connector: limite de requisições, Host público e resumo só do cache."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from radar_papers_mcp.config import Config
from radar_papers_mcp.fetcher.base import Paper
from radar_papers_mcp.mcp_server.limite import LimitadorPorOrigem, origem_da_requisicao
from radar_papers_mcp.mcp_server.tools import papers as tools_papers
from radar_papers_mcp.mcp_server.tools.papers import resumir_paper
from radar_papers_mcp.store.db import conectar
from radar_papers_mcp.store.queries import gravar, gravar_resumo


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


@pytest.fixture
def sem_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Qualquer tentativa de falar com o LLM derruba o teste."""

    class Proibido:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("o modo connector não pode chamar o LLM")

    monkeypatch.setattr(tools_papers, "QwenClient", Proibido)


def _resumos(caminho_db: str) -> int:
    with conectar(caminho_db, somente_leitura=True) as conexao:
        linha = conexao.execute("SELECT count(*) FROM resumos").fetchone()
    return int(linha[0]) if linha else 0


# --- configuração --------------------------------------------------------


def test_padrao_continua_stdio() -> None:
    """Quem usa hoje não pode mudar de comportamento sem pedir."""
    config = Config(_env_file=None)  # type: ignore[call-arg]
    assert config.transporte == "stdio"
    assert config.modo_connector is False
    assert (config.http_host, config.http_path, config.http_stateless) == (
        "127.0.0.1",
        "/mcp",
        True,
    )


def test_streamable_http_liga_o_modo_connector() -> None:
    assert Config(transporte="streamable-http").modo_connector is True


def test_hosts_publicos_aceitam_lista_por_virgula(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do .env vem texto, não JSON: sem NoDecode isso estourava."""
    monkeypatch.setenv("HTTP_HOSTS_PUBLICOS", "a.exemplo.ts.net, b.exemplo.ts.net,")
    assert Config(_env_file=None).http_hosts_publicos == [  # type: ignore[call-arg]
        "a.exemplo.ts.net",
        "b.exemplo.ts.net",
    ]


# --- resumo no modo connector --------------------------------------------


async def test_connector_serve_resumo_do_cache(caminho_db: str, sem_llm: None) -> None:
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper()], "calibração")
        gravar_resumo(
            conexao,
            chave="10.1101/abc",
            topico="calibração",
            problema="p",
            metodo="m",
            achado_principal="a",
            relevancia="r",
            modelo="x",
        )
    resposta = await resumir_paper(
        "10.1101/abc",
        caminho_db=caminho_db,
        qwen_endpoint="http://nao-usado/v1",
        qwen_model="x",
        somente_cache=True,
    )
    assert resposta.origem == "cache"
    assert resposta.resumo is not None and resposta.resumo.achado_principal == "a"


async def test_connector_sem_cache_diz_que_nao_ha_resumo(caminho_db: str, sem_llm: None) -> None:
    """Sem cache, nada de LLM, nada de resumo inventado e nada gravado."""
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper()], "calibração")
    resposta = await resumir_paper(
        "10.1101/abc",
        caminho_db=caminho_db,
        qwen_endpoint="http://nao-usado/v1",
        qwen_model="x",
        somente_cache=True,
    )
    assert resposta.resumo is None
    assert resposta.origem == "indisponivel"
    assert resposta.aviso is not None and "não há resumo" in resposta.aviso.lower()
    assert resposta.url.startswith("https://")
    assert _resumos(caminho_db) == 0


async def test_connector_sem_abstract_continua_sem_resumo(caminho_db: str, sem_llm: None) -> None:
    """A regra do projeto vale nos dois modos: sem abstract, não resumir."""
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper(abstract=None)], "calibração")
    resposta = await resumir_paper(
        "10.1101/abc",
        caminho_db=caminho_db,
        qwen_endpoint="http://nao-usado/v1",
        qwen_model="x",
        somente_cache=True,
    )
    assert resposta.resumo is None
    assert resposta.aviso is not None and "abstract" in resposta.aviso


async def test_connector_le_com_leitor_aberto(caminho_db: str, sem_llm: None) -> None:
    """O servidor só lê: outro leitor aberto no mesmo arquivo não atrapalha."""
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper()], "calibração")
    with conectar(caminho_db, somente_leitura=True):
        resposta = await resumir_paper(
            "10.1101/abc",
            caminho_db=caminho_db,
            qwen_endpoint="http://nao-usado/v1",
            qwen_model="x",
            somente_cache=True,
        )
    assert resposta.titulo == "Calibração de modelos clínicos"


async def test_tool_usa_so_cache_quando_transporte_e_http(
    caminho_db: str, sem_llm: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tool exposta lê o transporte da config e não chama o LLM no connector."""
    from radar_papers_mcp.mcp_server.server import mcp

    with conectar(caminho_db) as conexao:
        gravar(conexao, [_paper()], "calibração")
    monkeypatch.setenv("TRANSPORTE", "streamable-http")
    monkeypatch.setenv("DUCKDB_PATH", caminho_db)
    monkeypatch.chdir(tmp_path)  # sem .env do checkout interferindo

    resultado = await mcp.call_tool("resumir_paper", {"paper_id": "10.1101/abc"})
    texto = str(resultado)
    assert "indisponivel" in texto
    assert _resumos(caminho_db) == 0


# --- limite de requisições ----------------------------------------------


def test_limite_deixa_passar_ate_o_teto() -> None:
    limitador = LimitadorPorOrigem(3)
    assert [limitador.permitir("1.2.3.4") for _ in range(4)] == [True, True, True, False]


def test_limite_e_por_origem() -> None:
    """Um cliente em laço não pode consumir o limite dos outros."""
    limitador = LimitadorPorOrigem(2)
    assert limitador.permitir("1.1.1.1") and limitador.permitir("1.1.1.1")
    assert not limitador.permitir("1.1.1.1")
    assert limitador.permitir("2.2.2.2")


def test_origem_usa_forwarded_for_quando_ha_proxy() -> None:
    """Atrás de proxy, o IP do socket é o do proxy: todos viram a mesma origem."""
    scope = {
        "headers": [(b"x-forwarded-for", b"1.2.3.4, 203.0.113.9")],
        "client": ("127.0.0.1", 5000),
    }
    # Vale a entrada que o proxy local acrescentou (a última), não a do cliente.
    assert origem_da_requisicao(scope) == "203.0.113.9"


def test_origem_ignora_forwarded_for_de_quem_nao_e_proxy() -> None:
    scope = {
        "headers": [(b"x-forwarded-for", b"203.0.113.9")],
        "client": ("198.51.100.7", 5000),
    }
    assert origem_da_requisicao(scope) == "198.51.100.7"


def test_tabela_cheia_recusa(monkeypatch: pytest.MonkeyPatch) -> None:
    from radar_papers_mcp.mcp_server import limite

    monkeypatch.setattr(limite, "MAX_ORIGENS", 2)
    limitador = LimitadorPorOrigem(100)
    assert limitador.permitir("a") and limitador.permitir("b")
    assert not limitador.permitir("c")
    assert limitador.motivo_ultima_recusa == "tabela"


def test_origem_cai_para_o_socket_sem_proxy() -> None:
    assert origem_da_requisicao({"headers": [], "client": ("198.51.100.7", 1234)}) == "198.51.100.7"


def test_origem_desconhecida_nao_quebra() -> None:
    assert origem_da_requisicao({}) == "desconhecido"


def test_teto_global_protege_independente_da_origem() -> None:
    """O teto que importa para tráfego vindo do Claude: ele chega de poucos IPs."""
    limitador = LimitadorPorOrigem(limite_por_minuto=100, limite_global_por_minuto=3)
    assert all(limitador.permitir(f"10.0.0.{i}") for i in range(3))
    assert not limitador.permitir("10.0.0.99")  # origem nova, mas o global estourou
    assert limitador.motivo_ultima_recusa == "global"


def test_motivo_distingue_os_dois_tetos() -> None:
    limitador = LimitadorPorOrigem(limite_por_minuto=1, limite_global_por_minuto=100)
    assert limitador.permitir("1.1.1.1")
    assert not limitador.permitir("1.1.1.1")
    assert limitador.motivo_ultima_recusa == "origem"


def test_sem_teto_global_so_vale_o_por_origem() -> None:
    limitador = LimitadorPorOrigem(limite_por_minuto=2, limite_global_por_minuto=0)
    assert all(limitador.permitir(f"10.0.0.{i}") for i in range(50))


async def test_middleware_responde_429_acima_do_teto() -> None:
    from radar_papers_mcp.mcp_server.server import _com_limite

    chamadas: list[str] = []

    async def app(scope: object, receive: object, send: object) -> None:
        chamadas.append("app")

    enviados: list[dict[str, object]] = []

    async def send(mensagem: dict[str, object]) -> None:
        enviados.append(mensagem)

    async def receive() -> dict[str, object]:
        return {}

    protegido = _com_limite(app, limite_por_minuto=1, limite_global=10)
    scope = {"type": "http", "client": ("198.51.100.7", 1), "headers": []}
    await protegido(scope, receive, send)
    await protegido(scope, receive, send)

    assert chamadas == ["app"]
    assert enviados[0]["status"] == 429


# --- Host público --------------------------------------------------------


def test_sem_host_publico_mantem_o_padrao_do_sdk() -> None:
    """Sem nome público declarado, quem decide é o SDK: só loopback."""
    from radar_papers_mcp.mcp_server.server import _seguranca_de_transporte

    assert _seguranca_de_transporte(Config(http_hosts_publicos=[])) is None


def test_host_publico_entra_sem_derrubar_o_loopback() -> None:
    """Declarar o nome do túnel não pode cortar o acesso local, que é como se testa."""
    from radar_papers_mcp.mcp_server.server import _seguranca_de_transporte

    regras = _seguranca_de_transporte(Config(http_hosts_publicos=["mcp.exemplo.ts.net"]))
    assert regras is not None
    assert regras.enable_dns_rebinding_protection is True
    assert "mcp.exemplo.ts.net" in regras.allowed_hosts
    assert "127.0.0.1:*" in regras.allowed_hosts
    assert "https://mcp.exemplo.ts.net" in regras.allowed_origins


def test_host_de_fora_da_lista_continua_recusado() -> None:
    """A proteção contra DNS rebinding continua valendo para quem não foi declarado."""
    from mcp.server.transport_security import TransportSecurityMiddleware

    from radar_papers_mcp.mcp_server.server import _seguranca_de_transporte

    guarda = TransportSecurityMiddleware(
        _seguranca_de_transporte(Config(http_hosts_publicos=["mcp.exemplo.ts.net"]))
    )
    assert guarda._validate_host("mcp.exemplo.ts.net") is True
    assert guarda._validate_host("mcp.exemplo.ts.net:443") is True
    assert guarda._validate_host("site-do-atacante.exemplo") is False
    assert guarda._validate_host(None) is False
