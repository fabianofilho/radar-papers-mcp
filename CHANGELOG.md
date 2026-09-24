# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). O projeto
segue [versionamento semântico](https://semver.org/lang/pt-BR/).

## [0.1.0] - 2026-09-24

Primeira versão pública.

### O que há

- Servidor MCP (stdio) com duas tools:
  - `buscar_papers_novos(topico="", dias=7, limite=50)`: papers que entraram no PubMed ou
    no medRxiv no período, lidos da base local, com chave e link original.
  - `resumir_paper(paper_id)`: resumo estruturado (problema, método, achado, relevância)
    do abstract por um LLM local OpenAI-compatible, com cache e sem inventar resumo quando
    falta abstract.
- `papers-cli` com `topicos`, `sync`, `buscar`, `resumir`, `llm`, `schema` e
  `corrigir-datas`.
- Tópicos configuráveis em `config/topicos.yaml`, com query do PubMed (E-utilities) e
  termos do medRxiv (filtro local).
- Base DuckDB local, deduplicada por DOI, com acúmulo de tópicos por paper.
- Limitador de taxa da NCBI (3 req/s sem chave, 10 com chave) e User-Agent identificado.
- Units systemd do sync diário em `deploy/`.

### Mudou nesta versão

- "Novo" passa a ser decidido pela data de entrada na fonte (Entrez no PubMed, postagem no
  medRxiv), não pela data da edição da revista, que escondia a maioria dos papers do
  PubMed. O esearch usa `datetype=edat`. A base vai para o schema 2, com migração
  automática, e `papers-cli corrigir-datas` grava as datas Entrez dos papers antigos.
- A busca devolve o total real do período e avisa quando corta pelo `limite`; `dias` vai
  de 1 a 365. Erro inesperado na consulta não é mais relatado como sync em andamento.
- O tópico é comparado sem caixa e sem acento, por palavra inteira, e um tópico
  desconhecido devolve a lista dos válidos. `calibração` não casa mais com
  `multicalibração`.
- O sync pagina o PubMed até o `count` e o medRxiv até o total da janela, com aviso no log
  ao atingir o teto. Antes parava em 50 IDs e 500 preprints sem avisar.
- O prompt do resumo foi para dentro do pacote e é lido com `importlib.resources`; a
  instalação por wheel deixou de quebrar o `resumir_paper`.
- O agendador interno (APScheduler, `SYNC_HORA_LOCAL`) foi removido: o agendamento oficial
  é o timer systemd de `deploy/`.
- Dependência `mcp>=2.2,<3`, que é a série que tem `mcp.server.mcpserver`.
- `license = "MIT"` no pyproject, CHANGELOG e SECURITY.md.

### Limitações conhecidas

- O filtro do medRxiv é por trecho de texto e privilegia a cobertura: traz ruído, descrito
  no README.
- O resumo sai do abstract e não é conferido contra ele.

[0.1.0]: https://github.com/fabianofilho/radar-papers-mcp/commits/main
