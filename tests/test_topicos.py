"""Carregamento de config/topicos.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from radar_papers_mcp.config import TopicosInvalidos, carregar_topicos


def test_carrega_o_arquivo_real() -> None:
    topicos = carregar_topicos(Path(__file__).parents[1] / "config" / "topicos.yaml")
    assert topicos
    assert all(t.nome for t in topicos)
    assert any(t.pubmed for t in topicos)
    assert any(t.medrxiv for t in topicos)


def test_arquivo_inexistente(tmp_path: Path) -> None:
    with pytest.raises(TopicosInvalidos):
        carregar_topicos(tmp_path / "nao-existe.yaml")


def test_lista_vazia(tmp_path: Path) -> None:
    arquivo = tmp_path / "t.yaml"
    arquivo.write_text("topicos: []", encoding="utf-8")
    with pytest.raises(TopicosInvalidos):
        carregar_topicos(arquivo)


def test_topico_sem_nome(tmp_path: Path) -> None:
    arquivo = tmp_path / "t.yaml"
    arquivo.write_text("topicos:\n  - pubmed: algo", encoding="utf-8")
    with pytest.raises(TopicosInvalidos):
        carregar_topicos(arquivo)


def test_topico_so_com_pubmed(tmp_path: Path) -> None:
    arquivo = tmp_path / "t.yaml"
    arquivo.write_text("topicos:\n  - nome: x\n    pubmed: query", encoding="utf-8")
    topico = carregar_topicos(arquivo)[0]
    assert topico.medrxiv == ()
