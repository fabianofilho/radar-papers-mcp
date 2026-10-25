# radar-papers-mcp

Servidor MCP que monitora PubMed e medRxiv por tópicos de pesquisa configuráveis e resume
os papers com um LLM local. Guarda o resultado numa base local, deduplicado por DOI.

> ### Sobre o que esta ferramenta faz e não faz
>
> - **O resumo sai do abstract, não do texto completo.** Ele serve para triagem: decidir
>   se vale abrir o paper. Não substitui a leitura.
> - **O resumo é gerado por LLM e pode errar.** Confira os números no abstract original,
>   o link vem em toda resposta.
> - **A cobertura depende das suas queries.** O que não casa com a query configurada
>   simplesmente não aparece; ausência de resultado não significa ausência de literatura.

## Requisitos

| O quê | Versão | Para quê |
| --- | --- | --- |
| Python | 3.12+ | runtime |
| [uv](https://docs.astral.sh/uv/) | recente | dependências e venv |
| Um LLM local com API OpenAI-compatible | - | resumo dos papers |
| Chave da NCBI (opcional) | - | eleva o rate limit de 3 para 10 req/s |

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

### Sync agendado

O servidor MCP só lê a base; quem a alimenta é `papers-cli sync`. O agendamento oficial é
um timer systemd de usuário, versionado em [`deploy/`](deploy/): roda `sync --publicar`
todo dia às 04:10, com atraso aleatório de até 30 minutos (para várias máquinas não
baterem nas APIs públicas no mesmo minuto) e `Persistent=true` (se a máquina estava
desligada, roda ao ligar).

As units supõem o repositório em `~/radar-papers-mcp` e o uv em `~/.local/bin/uv`. Se for
diferente, ajuste `WorkingDirectory` e `ExecStart` antes de instalar.

```bash
cp deploy/radar-papers-sync.service deploy/radar-papers-sync.timer \
   deploy/radar-papers-sync-falha.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now radar-papers-sync.timer
systemctl --user list-timers radar-papers-sync.timer   # próxima execução
journalctl --user -u radar-papers-sync.service          # log dos syncs
```

Falha não passa em silêncio: se o PubMed (em qualquer tópico) ou o medRxiv falhar,
`papers-cli sync` grava o que as outras fontes trouxeram, lista as falhas no stderr e sai
com código 1. O systemd marca a unit como `failed` e o `OnFailure` roda
`radar-papers-sync-falha.service`, que grava uma linha com prioridade `err` no journal e,
se houver `notify-send`, mostra uma notificação. Para ser avisado por outro canal, troque
o `ExecStart` dessa unit. Para ver falhas recentes:

```bash
systemctl --user status radar-papers-sync.service
journalctl --user -t radar-papers-sync -p err
```

Sem o timer, rode `papers-cli sync` à mão ou pelo agendador que preferir; o servidor não
agenda nada sozinho.

### Atualizando uma base criada antes do schema 2

A versão atual decide o que é "novo" pela data de entrada na fonte (coluna
`data_entrada`). Uma base antiga é migrada sozinha na primeira conexão de escrita (o
próximo sync, por exemplo). Na migração, a data de entrada do PubMed é aproximada pelo
dia da última coleta; para gravar a data Entrez real dos papers que já estavam na base,
rode uma vez:

```bash
uv run papers-cli corrigir-datas
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

### Modo connector (servidor HTTP)

Por padrão o servidor fala **stdio**: o cliente sobe o processo na máquina de quem usa.
Com `TRANSPORTE=streamable-http`, ele vira um servidor alcançável pela rede, que é o que
o Claude aceita como custom connector.

```bash
TRANSPORTE=streamable-http HTTP_PORTA=8000 uv run radar-papers-mcp
```

| Variável | Padrão | Observação |
| --- | --- | --- |
| `TRANSPORTE` | `stdio` | `streamable-http` liga o modo connector |
| `HTTP_HOST` / `HTTP_PORTA` / `HTTP_PATH` | `127.0.0.1` / `8000` / `/mcp` | |
| `HTTP_STATELESS` | `true` | cada requisição independente; escala melhor |
| `HTTP_LIMITE_GLOBAL_POR_MINUTO` | `1200` | o teto que protege a máquina |
| `HTTP_LIMITE_POR_MINUTO` | `600` | por origem, contra chamada direta |
| `HTTP_HOSTS_PUBLICOS` | vazio | nomes públicos aceitos no `Host`, separados por vírgula |

**O que muda no modo connector.** O servidor só lê a base e **não chama o LLM**:

- `buscar_papers_novos` funciona igual.
- `resumir_paper` devolve apenas o resumo que já estiver em cache (`origem: "cache"`).
  Sem cache, `resumo` vem nulo, `origem` vem `indisponivel` e `aviso` diz que não há
  resumo e que este servidor não gera resumos sob demanda. Nada é inventado e nada é
  gravado. Paper sem abstract continua sem resumo, com o aviso de sempre.

Os motivos: resumir sob demanda gastaria inferência de quem hospeda a cada chamada de
qualquer usuário, e gravar o cache exigiria escrever no arquivo que o próprio servidor lê
(o DuckDB recusa um escritor com leitor aberto). O cache é preenchido fora do servidor,
com `papers-cli resumir` ou pelo servidor em stdio.

**Por que dois limites.** Quando o Claude chama um connector remoto, as requisições chegam
dos **IPs da Anthropic**, não do usuário final. Limitar só por IP colocaria todos os
usuários no mesmo balde. O teto global é o que vale para esse tráfego; o por origem serve
contra quem chama o servidor direto. Atrás de proxy local, a origem vem da entrada mais à
direita do `X-Forwarded-For`, e o cabeçalho só é aceito quando o socket é loopback.

**Atrás de um proxy ou túnel, declare o nome público.** O SDK do MCP valida o cabeçalho
`Host` e responde **421 Invalid Host** ao que não reconhece (proteção contra DNS
rebinding). Atrás de um túnel o `Host` que chega é o nome público, então ele precisa
constar em `HTTP_HOSTS_PUBLICOS`. O loopback continua valendo junto.

A unit [`deploy/radar-papers-connector.service`](deploy/radar-papers-connector.service)
sobe o servidor em `127.0.0.1:8103`, só leitura. Quem expõe para fora é a camada da frente
(proxy com TLS ou túnel).

**O sync não interrompe quem está lendo.** `papers-cli sync --publicar` constrói a base
nova ao lado e troca as duas por rename no fim: quem já abriu continua no arquivo antigo
até fechar, quem abrir depois pega o novo. É o modo que o timer usa. Sem `--publicar`, o
sync grava direto na base e segura a escrita enquanto busca nas fontes, e nesse intervalo
as tools respondem com o aviso de que a base não pôde ser lida. A troca também serve ao
tamanho: o DuckDB não devolve o espaço das páginas antigas, e o upsert diário dos papers
já conhecidos levou o arquivo a 9,3 MB contra 3,5 MB compactado em 11/10/2026. A base nova
é clonada da servida com `COPY FROM DATABASE`, e a cópia já sai compactada.

## Uso

### `buscar_papers_novos(topico="", dias=7, limite=50)`

Papers que entraram no PubMed ou no medRxiv nos últimos `dias`, lidos da base local, com
a chave para usar no resumo e o link original.

- **"Novo" é pela data de entrada na fonte** (`data_entrada`: Entrez no PubMed, postagem
  no medRxiv), não pela data da edição da revista (`data_publicacao`, só para exibição).
  A edição pode estar meses antes ou depois da entrada, e muitas revistas informam só o
  ano ou o mês.
- `topico` é o nome de um tópico configurado. Caixa e acento não importam, e basta um
  trecho com palavras inteiras do nome (`fairness`, `calibracao`). A comparação é por
  palavra inteira: `calibração` não traz `multicalibração`. Se o pedido não corresponder a
  um único tópico, a resposta vem vazia com a lista dos tópicos válidos em `aviso`.
- `dias` vai de 1 a 365 e `limite` de 1 a 200. Os mais recentes vêm primeiro.
- `total` conta todos os papers do período, mesmo os que ficaram além do `limite`; nesse
  caso `aviso` diz quantos foram omitidos.

### `resumir_paper(paper_id: str)`

```json
{
  "chave": "pubmed:42761253",
  "titulo": "Mortality risk ranking after medical emergency team review...",
  "url": "https://pubmed.ncbi.nlm.nih.gov/42761253/",
  "origem": "llm",
  "resumo": {
    "problema": "Validação externa e redesenvolvimento de modelos preditivos de mortalidade após revisão da equipe de emergência.",
    "metodo": "Coorte multicêntrica em quatro hospitais, 1.937 adultos.",
    "achado_principal": "O modelo original discriminou bem (AUC 0,80) mas com estimativas variáveis entre hospitais; o novo modelo chegou a AUC 0,84 com menos variáveis.",
    "relevancia": "Mostra que a discriminação pode ser robusta mesmo quando a calibração absoluta varia entre instituições."
  },
  "aviso": "O resumo é gerado por LLM local a partir do abstract, não do texto completo. Sempre confira no link original antes de citar."
}
```

O conteúdo do resumo é de um retorno real (a `chave` acima é ilustrativa). O resumo fica
cacheado: o mesmo paper não é resumido duas vezes. Sem abstract, ou com o LLM fora do ar,
`resumo` vem nulo, `origem` vem `indisponivel` e `aviso` explica o motivo. No modo
connector o LLM não é chamado: só vem resumo que já estiver em cache (veja "Modo
connector").

## As duas fontes

| Fonte | API | Limite |
| --- | --- | --- |
| PubMed | E-utilities (`esearch` + `efetch`) | **3 req/s sem chave, 10 com chave** |
| medRxiv | `api.medrxiv.org/details` | sem busca por termo; paginado de 100 em 100 |

O sync pagina as duas fontes até o fim: o PubMed até o `count` do esearch (teto de 1000
IDs por tópico) e o medRxiv até o total da janela (teto de 100 páginas, 10 mil preprints).
Se um teto for atingido, o log do sync registra um aviso com o total da fonte.

O rate limit da NCBI é aplicado de verdade, a primeira tentativa de teste deste projeto
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

**A janela do medRxiv custa caro.** Uma semana do medRxiv tem por volta de mil preprints, e
o sync baixa todos para filtrar localmente (cerca de 10 requisições). Um backfill largo
(`sync --dias 90`) baixa muito mais; acima de 10 mil preprints na janela, o que passar do
teto fica de fora e o log avisa.

**O filtro do medRxiv privilegia a cobertura, não a precisão.** Um preprint casa com o
tópico quando qualquer termo da lista aparece como trecho do título ou do abstract. Isso é
intencional, para não perder preprint relevante, mas traz ruído: no tópico de calibração
aparecem preprints que falam de calibrar um instrumento ou um modelo econômico, sem relação
com modelo clínico. A triagem fica com você (ou com o `resumir_paper`, cujo campo
`relevancia` diz quando o paper só tangencia o tópico). Para reduzir o ruído, use termos
mais específicos no YAML.

**A query do PubMed é sua responsabilidade.** Uma query mal formada devolve zero sem erro.
Teste no [PubMed](https://pubmed.ncbi.nlm.nih.gov/) antes de colocar no YAML, na prática,
buscas muito específicas devolvem pouquíssimo (`multicalibration` retorna ~14 resultados
em toda a base).

**O resumo não é verificado contra o abstract.** Diferente do projeto de protocolos, aqui
não há conferência de citação literal: o campo `achado_principal` pode conter um número
que o modelo interpretou errado.

**Só PubMed e medRxiv.** Sem arXiv, bioRxiv, Scopus ou Web of Science.

## Privacidade

- **Sai da máquina:** requisições ao PubMed (NCBI) e ao medRxiv. Se você configurar uma
  chave da NCBI, ela vai junto nas requisições, é o funcionamento normal da API.
- **Não sai:** seus tópicos de pesquisa ficam no arquivo local; as queries vão às APIs
  como qualquer busca.
- O abstract vai para o seu LLM no resumo.
- Sem telemetria, sem analytics.

## Contribuindo

Veja [CONTRIBUTING.md](CONTRIBUTING.md). PubMed e medRxiv são serviços públicos: não rode
sincronização em loop nem contorne o limitador de taxa.

## Licença e atribuição

[MIT](LICENSE): este projeto é agregação de literatura e metadados abertos, sem contato
com regulação, conduta clínica ou dado de paciente.

Construído no contexto do [IA.med](https://iamed.cc).
