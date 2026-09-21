# radar-papers-mcp

Servidor MCP que monitora PubMed e medRxiv por tópicos de pesquisa configuráveis e resume
os papers com um LLM local. Guarda o resultado numa base local, deduplicado por DOI.

> ### Sobre o que esta ferramenta faz e não faz
>
> - **O resumo sai do abstract, não do texto completo.** Ele serve para triagem: decidir
>   se vale abrir o paper. Não substitui a leitura.
> - **O resumo é gerado por LLM e pode errar.** Confira os números no abstract original —
>   o link vem em toda resposta.
> - **A cobertura depende das suas queries.** O que não casa com a query configurada
>   simplesmente não aparece; ausência de resultado não significa ausência de literatura.

## Requisitos

| O quê | Versão | Para quê |
| --- | --- | --- |
| Python | 3.12+ | runtime |
| [uv](https://docs.astral.sh/uv/) | recente | dependências e venv |
| Um LLM local com API OpenAI-compatible | — | resumo dos papers |
| Chave da NCBI (opcional) | — | eleva o rate limit de 3 para 10 req/s |

## Instalação

```bash
git clone https://github.com/fabianofilho/radar-papers-mcp.git
cd radar-papers-mcp
uv sync
cp .env.example .env
```

## Configuração

| Variável | Padrão | Observação |
| --- | --- | --- |
| `QWEN_ENDPOINT` | `http://127.0.0.1:8080/v1` | llama.cpp. Ollama: `:11434/v1`. LM Studio: `:1234/v1` |
| `QWEN_MODEL` | `local-model` | llama.cpp e LM Studio aceitam qualquer nome |
| `PUBMED_API_KEY` | vazio | [chave gratuita da NCBI](https://ncbiinsights.ncbi.nlm.nih.gov/2017/11/02/new-api-keys-for-the-e-utilities/) |
| `DUCKDB_PATH` | `./data/papers.duckdb` | base local |
| `TOPICOS_PATH` | `./config/topicos.yaml` | tópicos monitorados |
| `SYNC_HORA_LOCAL` | `04:10` | horário fixo do sync agendado |

Os tópicos ficam em `config/topicos.yaml`. Cada um tem **duas** configurações, porque as
fontes funcionam de forma diferente:

```yaml
topicos:
  - nome: multicalibração
    pubmed: multicalibration[All Fields]   # sintaxe das E-utilities
    medrxiv:                                # palavras simples, filtradas localmente
      - multicalibration
      - multi-calibration
```

```bash
uv run papers-cli topicos
uv run papers-cli sync --dias 30
uv run papers-cli buscar "multicalibração"
uv run papers-cli resumir "10.1016/j.exemplo.2026.100217"
```

### Ligando ao Claude Code

```bash
claude mcp add radar-papers --scope user \
  -e DUCKDB_PATH=/caminho/para/radar-papers-mcp/data/papers.duckdb \
  -e TOPICOS_PATH=/caminho/para/radar-papers-mcp/config/topicos.yaml \
  -e QWEN_ENDPOINT=http://127.0.0.1:8080/v1 \
  -e QWEN_MODEL=local-model \
  -- uv --directory /caminho/para/radar-papers-mcp run radar-papers-mcp
```

## Uso

### `buscar_papers_novos(topico="", dias=7)`

Papers do período na base local, com a chave para usar no resumo e o link original.

### `resumir_paper(paper_id: str)`

```json
{
  "titulo": "Mortality risk ranking after medical emergency team review…",
  "url": "https://pubmed.ncbi.nlm.nih.gov/42761253/",
  "origem": "llm",
  "resumo": {
    "problema": "Validação externa e redesenvolvimento de modelos preditivos de mortalidade após revisão da equipe de emergência.",
    "metodo": "Coorte multicêntrica em quatro hospitais, 1.937 adultos.",
    "achado_principal": "O modelo original discriminou bem (AUC 0,80) mas com estimativas variáveis entre hospitais; o novo modelo chegou a AUC 0,84 com menos variáveis.",
    "relevancia": "Mostra que a discriminação pode ser robusta mesmo quando a calibração absoluta varia entre instituições."
  }
}
```

Esse é um retorno real. O resumo fica cacheado: o mesmo paper não é resumido duas vezes.

## As duas fontes

| Fonte | API | Limite |
| --- | --- | --- |
| PubMed | E-utilities (`esearch` + `efetch`) | **3 req/s sem chave, 10 com chave** |
| medRxiv | `api.medrxiv.org/details` | sem busca por termo; paginado de 100 em 100 |

O rate limit da NCBI é aplicado de verdade — a primeira tentativa de teste deste projeto
recebeu `{"error": "API rate limit exceeded"}`. O fetcher espaça as requisições conforme a
chave configurada.

O medRxiv **não aceita busca por termo**: a API é consultada uma vez por janela e o filtro
por tópico é local, sobre título e abstract.

## Deduplicação por DOI

O mesmo paper casa com mais de um tópico, e um preprint do medRxiv pode sair depois num
periódico indexado no PubMed com o mesmo DOI. A chave é o DOI (ou o id da fonte quando não
há DOI), e um paper já conhecido **ganha o novo tópico na lista** em vez de virar uma
segunda linha.

## Limitações conhecidas

**Sem abstract, não há resumo.** Um paper sem abstract devolve erro explícito em vez de um
resumo gerado a partir do título. Resumir texto vazio produz exatamente o tipo de invenção
plausível que torna a ferramenta inútil para pesquisa.

**A janela do medRxiv custa caro.** Uma janela larga traz milhares de preprints que serão
descartados no filtro local. O padrão é limitado a 5 páginas (500 preprints) por execução.

**A query do PubMed é sua responsabilidade.** Uma query mal formada devolve zero sem erro.
Teste no [PubMed](https://pubmed.ncbi.nlm.nih.gov/) antes de colocar no YAML — na prática,
buscas muito específicas devolvem pouquíssimo (`multicalibration` retorna ~14 resultados
em toda a base).

**O resumo não é verificado contra o abstract.** Diferente do projeto de protocolos, aqui
não há conferência de citação literal: o campo `achado_principal` pode conter um número
que o modelo interpretou errado.

**Só PubMed e medRxiv.** Sem arXiv, bioRxiv, Scopus ou Web of Science.

## Privacidade

- **Sai da máquina:** requisições ao PubMed (NCBI) e ao medRxiv. Se você configurar uma
  chave da NCBI, ela vai junto nas requisições — é o funcionamento normal da API.
- **Não sai:** seus tópicos de pesquisa ficam no arquivo local; as queries vão às APIs
  como qualquer busca.
- O abstract vai para o seu LLM no resumo.
- Sem telemetria, sem analytics.

## Contribuindo

Veja [CONTRIBUTING.md](CONTRIBUTING.md). PubMed e medRxiv são serviços públicos: não rode
sincronização em loop nem contorne o limitador de taxa.

## Licença e atribuição

[MIT](LICENSE) — este projeto é agregação de literatura e metadados abertos, sem contato
com regulação, conduta clínica ou dado de paciente.

Construído no contexto do [IA.med](https://iamed.cc).
