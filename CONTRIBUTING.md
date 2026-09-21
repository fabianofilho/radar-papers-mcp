# Contribuindo

Obrigado pelo interesse. Este e um projeto pequeno, mantido por uma pessoa so, entao
issues e PRs objetivos sao os mais faceis de tratar.

## Rodando localmente

Requer [uv](https://docs.astral.sh/uv/) e Python 3.12+.

```bash
uv sync
cp .env.example .env     # ajuste o endpoint do seu LLM local
uv run pytest -q         # testes
uv run ruff check .      # lint
uv run ruff format .     # formatacao
uv run mypy              # tipos
```

Os testes rodam **offline**: as respostas das APIs externas estao mockadas com `respx`, e
as fixtures foram capturadas de respostas reais. Nao e preciso rede nem LLM para testar.

## Padrao de commit

Assunto no imperativo, em uma linha curta, seguido de um corpo explicando **por que** a
mudanca e necessaria. Se a mudanca veio de um comportamento observado (um parser que
quebrou, uma API que respondeu diferente), descreva o caso concreto.

Antes de abrir o PR, rode os quatro comandos acima. O CI roda os mesmos.

## Nao rode sincronizacao em loop

O PubMed (NCBI) e o medRxiv sao servicos publicos e gratuitos, mantidos com dinheiro publico e
nao dimensionados para volume automatizado.

- Nao rode o sync em loop, nem reduza o intervalo entre requisicoes para testar.
- Para desenvolver e testar, use as fixtures do diretorio `tests/fixtures/` em vez de
  bater na API de verdade.
- Se precisar de uma coleta real durante o desenvolvimento, use os limites que a CLI
  oferece (`papers-cli sync --dias 7`).
- Um PR que aumente a frequencia de acesso as fontes precisa justificar por que.

O rate limit da NCBI e aplicado de verdade: passar de 3 requisicoes por segundo
sem chave devolve erro. Uma chave gratuita eleva o limite para 10/s, configure em
`PUBMED_API_KEY` em vez de contornar o limitador.
