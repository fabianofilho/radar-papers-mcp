"""Configuração e tópicos monitorados."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger(__name__)


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    qwen_endpoint: str = Field(default="http://127.0.0.1:8080/v1")
    qwen_model: str = Field(default="local-model")
    qwen_timeout_segundos: float = Field(default=180.0)

    pubmed_api_key: str = Field(default="")
    duckdb_path: Path = Field(default=Path("./data/papers.duckdb"))
    topicos_path: Path = Field(default=Path("./config/topicos.yaml"))
    log_level: str = Field(default="INFO")

    # --- modo connector (servidor HTTP público) ---
    # Por padrão o servidor fala stdio: o cliente sobe o processo na máquina de
    # quem usa. Em "streamable-http" ele vira um servidor alcançável pela rede,
    # que é o que o Claude aceita como custom connector.
    transporte: Literal["stdio", "streamable-http"] = Field(default="stdio")
    http_host: str = Field(default="127.0.0.1")
    http_porta: int = Field(default=8000, ge=1, le=65535)
    http_path: str = Field(default="/mcp")
    # Stateless: cada requisição é independente, sem sessão guardada no servidor.
    # Escala melhor e simplifica o deploy; para duas tools de consulta, basta.
    http_stateless: bool = Field(default=True)
    # Dois tetos por minuto. O global é o que protege a máquina: quando o
    # Claude.ai chama um connector, as requisições chegam dos IPs da Anthropic,
    # então limitar por IP colocaria todos os usuários no mesmo balde. O por
    # origem serve contra quem chama o servidor direto, fora do Claude.
    http_limite_global_por_minuto: int = Field(default=1200, ge=1)
    http_limite_por_minuto: int = Field(default=600, ge=1)
    # Nomes pelos quais o servidor aceita ser chamado, separados por vírgula.
    # Vazio significa só loopback. O SDK valida o cabeçalho Host contra esta
    # lista e responde 421 fora dela: é defesa contra DNS rebinding, onde um
    # site qualquer faz o navegador da vítima falar com um servidor local. Atrás
    # de um proxy ou túnel, o Host que chega é o nome público, então ele precisa
    # constar aqui; a alternativa seria desligar a checagem, que é pior.
    # NoDecode: sem ele o pydantic-settings tenta ler o valor do .env como JSON
    # antes do validador rodar, e uma lista separada por vírgula estoura.
    http_hosts_publicos: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("http_hosts_publicos", mode="before")
    @classmethod
    def _dividir_hosts(cls, valor: object) -> object:
        """Aceita "a.exemplo,b.exemplo" do .env, não só lista JSON."""
        if isinstance(valor, str):
            return [p.strip() for p in valor.split(",") if p.strip()]
        return valor

    @property
    def modo_connector(self) -> bool:
        """No modo connector o servidor não chama o LLM nem escreve na base.

        Resumir sob demanda exigiria inferência paga por quem hospeda e escrita
        no arquivo que o próprio servidor lê, e o DuckDB recusa abrir para
        escrita com um leitor aberto. No connector, resumir_paper só devolve o
        que já está no cache, gerado fora do servidor (papers-cli resumir ou o
        servidor em stdio).
        """
        return self.transporte != "stdio"


@dataclass(frozen=True)
class Topico:
    """Um tópico monitorado, com a query de cada fonte."""

    nome: str
    pubmed: str | None = None
    medrxiv: tuple[str, ...] = field(default_factory=tuple)


class TopicosInvalidos(ValueError):
    """O arquivo de tópicos não está no formato esperado."""


def carregar_topicos(caminho: Path | str) -> list[Topico]:
    """Lê config/topicos.yaml."""
    arquivo = Path(caminho)
    if not arquivo.exists():
        raise TopicosInvalidos(f"arquivo de tópicos não encontrado: {arquivo}")

    dados = yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}
    brutos = dados.get("topicos")
    if not isinstance(brutos, list) or not brutos:
        raise TopicosInvalidos("o arquivo precisa ter uma lista 'topicos' não vazia")

    topicos: list[Topico] = []
    for item in brutos:
        if not isinstance(item, dict) or not item.get("nome"):
            raise TopicosInvalidos(f"tópico sem 'nome': {item!r}")
        medrxiv = item.get("medrxiv") or []
        topicos.append(
            Topico(
                nome=str(item["nome"]).strip(),
                pubmed=str(item["pubmed"]).strip() if item.get("pubmed") else None,
                medrxiv=tuple(str(t).strip() for t in medrxiv if str(t).strip()),
            )
        )
    return topicos


def carregar_config() -> Config:
    return Config()
