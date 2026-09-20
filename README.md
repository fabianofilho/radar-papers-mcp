# radar-papers-mcp

Servidor MCP que monitora PubMed e medRxiv por tópicos de pesquisa configuráveis e resume
os papers com o LLM local — sem consumir quota de busca na nuvem.

## As duas fontes

| Fonte | API | Limite |
| --- | --- | --- |
| PubMed | E-utilities (`esearch` + `efetch`) | **3 req/s sem chave, 10 com chave** |
| medRxiv | `api.medrxiv.org/details` | Sem busca por termo; paginado de 100 em 100 |

O rate limit da NCBI é aplicado de verdade — na primeira tentativa de teste ele devolveu
`{"error": "API rate limit exceeded"}`. O fetcher espaça as requisições conforme a chave
configurada. Uma chave gratuita triplica o limite: vale configurar em `PUBMED_API_KEY`.

O medRxiv **não aceita busca por termo**, então a API é consultada uma vez por janela e o
filtro por tópico é aplicado localmente sobre título e abstract. Por isso os tópicos têm
duas configurações diferentes: uma query de E-utilities para o PubMed e uma lista de
palavras simples para o medRxiv.

## Tópicos

Configurados em `config/topicos.yaml`:

```yaml
topicos:
  - nome: multicalibração
    pubmed: multicalibration[All Fields]
    medrxiv:
      - multicalibration
      - multi-calibration
```

`papers-cli topicos` lista o que está configurado.

## Deduplicação por DOI

O mesmo paper casa com mais de um tópico, e um preprint do medRxiv pode sair depois num
periódico indexado no PubMed com o mesmo DOI. A chave é o DOI (ou o id da fonte quando não
há DOI), e um paper que já existe **ganha o novo tópico na lista** em vez de virar uma
segunda linha.

## Rodando

```bash
uv sync
cp .env.example .env
uv run papers-cli llm                 # confirma o LLM local
uv run papers-cli topicos
uv run papers-cli sync --dias 30
uv run papers-cli buscar "multicalibração"
uv run papers-cli resumir "10.1101/2026.09.15.12345"
uv run radar-papers-mcp               # servidor MCP no stdio
```

## Tools

### `buscar_papers_novos(topico="", dias=7)`
Papers do período na base local, com a chave para usar no resumo e o link original.

### `resumir_paper(paper_id)`
Resumo estruturado: problema, método, achado principal e relevância ao tópico. Fica
cacheado no DuckDB — o mesmo paper não é resumido duas vezes.

## Sem abstract, sem resumo

Um paper sem abstract devolve erro explícito em vez de um resumo gerado do título. Resumir
um texto vazio produz exatamente o tipo de invenção plausível que torna a ferramenta
inútil para pesquisa.

O resumo sai do **abstract**, não do texto completo, e vem sempre com o link original.
