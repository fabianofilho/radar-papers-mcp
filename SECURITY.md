# Segurança

## Como reportar

Não abra issue pública para uma vulnerabilidade. Use o
[reporte privado do GitHub](https://github.com/fabianofilho/radar-papers-mcp/security/advisories/new),
com a descrição, os passos para reproduzir e a versão (ou commit) afetada. Se o reporte
privado não estiver disponível, abra uma issue pedindo um canal privado, sem detalhes da
falha. O projeto é mantido por uma pessoa só: a resposta é feita no
melhor esforço, sem prazo garantido.

## Escopo

Entra no escopo:

- O servidor MCP e o `papers-cli` deste repositório.
- O tratamento das respostas do PubMed, do medRxiv e do LLM local (por exemplo, XML ou
  JSON malformado que leve a execução de código ou escrita fora da base).
- Vazamento da chave da NCBI (`PUBMED_API_KEY`) para fora das requisições à própria NCBI.

Fica fora:

- As APIs do PubMed e do medRxiv e o LLM local que você configurar.
- O conteúdo dos resumos: eles são gerados por LLM e podem errar, o que é uma limitação
  documentada, não uma vulnerabilidade.
- Instalações que exponham o servidor além do stdio local, que é o único transporte que
  o projeto oferece.
