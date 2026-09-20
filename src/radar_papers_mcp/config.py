"""Configuração e tópicos monitorados."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    qwen_endpoint: str = Field(default="http://127.0.0.1:11434/v1")
    qwen_model: str = Field(default="local-model")
    qwen_timeout_segundos: float = Field(default=180.0)

    pubmed_api_key: str = Field(default="")
    duckdb_path: Path = Field(default=Path("./data/papers.duckdb"))
    topicos_path: Path = Field(default=Path("./config/topicos.yaml"))
    # Horário fixo: este é o mais pesado, então fecha a fila de madrugada.
    sync_hora_local: str = Field(default="04:10", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    log_level: str = Field(default="INFO")


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
