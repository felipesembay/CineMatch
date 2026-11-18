# CineMatch — Estudo de viabilidade do monitoramento de recomendação

**Status:** Proposta técnica para avaliação  
**Data:** 2026-11-11  
**Escopo:** Backend FastAPI, pipelines de avaliação e observabilidade; frontend apenas emite eventos.  
**Premissa:** catálogo inicial de 8.000 títulos; ainda sem interações reais de usuários. Valores de benchmarks anteriores são *proxies*, não evidência de preferência.

## 1. Objetivo

Definir métricas, dependências, arquitetura e critérios de aceite para monitorar o CineMatch sem confundir qualidade semântica offline, comportamento observado e desempenho operacional. Este documento é uma **proposta**, não um relatório de implementação concluída.

## 2. Inventário do que já existe e do que falta confirmar

O contrato previsto inclui `GET /movies/{id}/similar`, `GET /recommendations/me`, `POST /events`, `POST /ratings`, preferências e favoritos. `RecommendationList` inclui `request_id`, `model_version`, `fallback` e itens com `score` e `reason`.

Eventos previstos: `impression`, `click`, `favorite_add`, `favorite_remove`, `rating`, `search`, com `event_id`, `surface`, `position`, `request_id`, `model_version`, `occurred_at`, `item_id`/`query` quando aplicável. O frontend prevê impressões quando ≥50% visível por ≥1 segundo e envio em lote.

**Verificar no repositório antes de desenvolver:** persistência real dos eventos; associação de eventos a usuários/sessões; persistência das listas servidas; semântica de `request_id` (requisição HTTP versus lista de recomendações); IDs de modelo e de encoder; disponibilidade dos embeddings; pipelines de avaliação existentes; jobs e monitoramento atual; retenção e consentimento dos dados.

## 3. Matriz de viabilidade e prioridade

| Métrica | Disponível sem usuários? | Dados necessários | Complexidade | Prioridade |
|---|---|---|---|---|
| Franchise Hit@10 (proxy) | Sim | Franquias verificadas, recomendações | Baixa–média | P0 |
| Genre Overlap@10 (proxy) | Sim | Gêneros, recomendações | Baixa | P0 |
| Proxy NDCG@10 | Sim, com rótulos proxy | Relevância proxy por candidato e consulta | Média | P1 |
| Intra-List Diversity@K | Sim | Embeddings comparáveis, listas | Baixa | P0 |
| Catalog Coverage@K | Sim | Listas de múltiplas consultas, catálogo elegível | Baixa | P0 |
| Fallback Rate | Sim | Respostas servidas com `fallback` | Baixa | P0 |
| Latência p50/p95/p99 e taxa de erro | Sim | Telemetria do servidor | Baixa | P0 |
| Distribuição de gêneros / JS drift | Sim | Baseline, listas e gêneros | Média | P1 |
| CTR e CTR por posição | Não | Impressões e cliques atribuídos | Média | P1 após tráfego |
| Favorite Rate | Não | Impressões e eventos de favoritos | Média | P1 após tráfego |
| Rating Rate e nota positiva | Não | Avaliações e exposição atribuída | Média | P2 |
| NDCG@K, MAP@K, Recall@K com usuários | Não | Ground truth construído em janela temporal, conjunto elegível | Alta | P2 |
| Novelty / popularidade | Parcialmente | Popularidade histórica ou número de interações | Média | P2 |
| Personalization entre usuários | Não | Listas por usuário, elegibilidade e amostragem | Média | P2 |
| Embedding drift | Sim, com cautela | Embeddings, versão do encoder, amostra fixa | Média–alta | P2 |

P0 = implementação inicial; P1 = segunda etapa; P2 = fase posterior. A disponibilidade indicada depende de confirmar que os dados são de fato persistidos.

## 4. Definições matemáticas e cuidados

### 4.1 Métricas proxy offline

- **Franchise Hit@K:** proporção de consultas elegíveis em que ao menos um item do Top-K pertence à mesma franquia verificada. Excluir a própria consulta; reportar também o número de consultas elegíveis. Não chamar de acurácia.
- **Genre Overlap@K:** média, sobre os itens recomendados, de `1[genres(query) ∩ genres(item) ≠ ∅]`. Reportar consultas sem gênero separadamente; alta sobreposição não implica satisfação.
- **Proxy NDCG@K:** `DCG@K / IDCG@K`, com `DCG = Σ (2^rel_i - 1) / log2(i+1)` e rótulos proxy documentados. Não misturar com NDCG calculado a partir de usuários.
- **Validação:** GroupKFold por franquia quando houver treinamento supervisionado; separar conjuntos de treino/validação/teste; registrar versão do catálogo, dataset, encoder e modelo.

### 4.2 Diversidade e cobertura

- **ILD@K:** média de `1 - cosine_similarity(e_i, e_j)` entre pares distintos na mesma lista. Para vetores normalizados, a distância pode variar de 0 a 2; não presumir faixa [0,1]. Listas com menos de dois itens retornam `null` e um motivo.
- **Catalog Coverage@K:** `|união dos itens recomendados em N consultas| / |catálogo elegível|`. Fixar conjunto de consultas, janela, superfície, versão e K para comparabilidade. Relatar cobertura por gênero e tipo.
- **Novelty:** preferir `-log2(p(item))`, com probabilidade de popularidade suavizada e janela fixa; não confundir popularidade editorial com comportamento de usuários.
- **Personalization:** distância média entre listas de usuários distintos, controlando elegibilidade e perfil; exige amostra e proteção de privacidade.

### 4.3 Interações reais

- **CTR:** `cliques atribuídos / impressões válidas`; calcular por superfície, posição, `model_version`, dispositivo e janela. Definir janela de atribuição (proposta inicial: 24 h, sujeita a validação). Cliques não são rótulos de relevância isentos de viés de posição.
- **Favorite Rate:** `adições aos favoritos atribuídas / impressões válidas`. Considerar apenas adições novas e não contar toggles repetidos como conversões independentes.
- **Rating Rate:** `avaliações atribuídas / impressões válidas`; **Positive Rating Rate:** `avaliações ≥4 / avaliações válidas`. Mostrar tamanho amostral.
- **NDCG/Recall/MAP reais:** dependem de definição explícita de relevância, candidatos elegíveis, período de observação e tratamento de itens não expostos. Ausência de clique **não** equivale automaticamente a preferência negativa. Comparação causal requer A/B randomizado ou métodos apropriados de correção de viés.

### 4.4 Drift

- **Jensen–Shannon divergence:** comparar distribuições categóricas (ex.: gêneros recomendados) contra baseline fixa; usar normalização e suavização consistentes; atenção a títulos multigênero (contagem por item ou distribuição de etiquetas, documentar).
- **PSI:** opcional para variáveis numéricas, com bins definidos na baseline; limiares comuns de mercado não são universais.
- **Embedding drift:** comparar amostras no **mesmo encoder e espaço vetorial**; mudança de encoder exige avaliação própria, não comparação direta de centroides.
- Drift sinaliza mudança, não prova degradação; alertas devem acionar diagnóstico antes de qualquer retreino automático.

### 4.5 Serving

- **Latency:** histogramas e percentis p50/p95/p99 por rota e versão; diferenciar tempo de API de latência percebida no navegador.
- **Error Rate:** proporção de respostas 5xx (e 4xx separadamente) por rota; definir denominador.
- **Fallback Rate:** respostas com `fallback=true` / respostas de recomendação; segmentar por endpoint e motivo de fallback.
- **Neighbor Availability:** itens elegíveis com pelo menos K vizinhos válidos / itens elegíveis; distinguir ausência de vizinhos de erro de consulta.

## 5. Instrumentação e contrato de eventos

### 5.1 Contrato existente

Preservar `POST /events` com `{events: Event[]}`, `event_id` UUID para idempotência, `occurred_at` ISO, `surface`, `position`, `item_id`, `query`, `request_id`, `model_version` conforme aplicável. Validar com Pydantic no backend e Zod no frontend.

### 5.2 Extensões propostas (não implementar sem revisar compatibilidade)

1. `recommendation_list_id` UUID para distinguir uma lista renderizada de uma requisição HTTP.
2. `anonymous_session_id` pseudônimo e `user_id` interno somente no backend autenticado; nunca confiar em `user_id` fornecido pelo cliente.
3. `experiment_id` / `variant_id` para futuros testes A/B.
4. `event_schema_version` e `client_app_version` para evolução do contrato.
5. Registro server-side da resposta de recomendação (`request_id`, `model_version`, `fallback`, `surface`, `item_ids` ordenados, horário) para atribuição auditável.

Evitar registrar e-mails, senhas, tokens e consultas sensíveis em logs de monitoramento. Definir retenção, controle de acesso e política LGPD antes da coleta em produção.

### 5.3 Qualidade dos eventos

- Índice único em `event_id`; inserção idempotente (`ON CONFLICT DO NOTHING`).
- `impression`: ≥50% visível por ≥1 s, uma vez por item por lista; manter contador de exposições distintas entre visitas.
- Validar posições, superfície, timestamp plausível e associação entre item e lista servida.
- Tratar perda de eventos, bloqueio de beacon, atraso e duplicatas. `sendBeacon` é best-effort, não garantia de entrega.
- Exibir contagem de eventos inválidos, atrasados, duplicados e sem atribuição.

## 6. Arquitetura proposta

```text
React (eventos) ──POST /events──> FastAPI ──> PostgreSQL (eventos)
                                        │
FastAPI (resposta de recomendações) ────┴──> PostgreSQL (listas servidas)
                                                  │
                              jobs agendados de agregação/avaliação
                                                  │
                   tabelas métricas históricas + MLflow (avaliação offline)
                                                  │
                                 dashboard / alertas / investigação
```

**Separação de responsabilidades:** API serve recomendações e recebe eventos; jobs calculam métricas; MLflow rastreia experimentos e avaliações offline; PostgreSQL guarda séries históricas. Evitar cálculos pesados no caminho síncrono da API.

### Esquema mínimo sugerido

- `recommendation_serves(id, request_id, recommendation_list_id, user_key, surface, model_version, fallback, served_at, items_json)`
- `recommendation_events(event_id PK, recommendation_list_id, user_key, type, item_id, position, surface, model_version, occurred_at, received_at)`
- `monitoring_metric_daily(date, model_version, surface, metric_name, metric_value, numerator, denominator, sample_size, metadata_json)`
- `offline_evaluation_runs(run_id, catalog_version, model_version, dataset_version, evaluated_at, metrics_json, parameters_json)`

**Nota:** são tabelas conceituais; normalizar `items_json` em tabela filha se for necessário consultar posições e itens em escala. Índices por `(occurred_at, model_version, surface)` e por identificadores de lista; particionamento somente se volume justificar.

## 7. Estrutura de código sugerida

```text
src/monitoring/
  metrics/
    ranking.py
    engagement.py
    diversity.py
    coverage.py
    drift.py
    serving.py
  jobs/
    daily_metrics.py
    offline_evaluation.py
    drift_detection.py
  repositories/
    event_repository.py
    metrics_repository.py
  alerts/
    rules.py
  tests/
    test_ranking.py
    test_engagement.py
    test_diversity.py
    test_event_idempotency.py
```

Funções de métricas devem ser puras, tipadas, testáveis e desacopladas de FastAPI/SQL. Funções de repositório e jobs cuidam de I/O. Denominadores zero devem retornar `None`/`NaN` com status `insufficient_data`, nunca 0 silenciosamente.

## 8. Plano de implementação

### Etapa 0 — Auditoria (pré-requisito)

- [ ] Inspecionar contrato real da API e o esquema do banco.
- [ ] Localizar pipelines de recomendação e avaliações já existentes.
- [ ] Verificar IDs de franquias, gêneros, embeddings e versão de catálogo.
- [ ] Confirmar como `request_id` é gerado e propagado.
- [ ] Medir volume de requisições e disponibilidade de telemetria.
- [ ] Definir baseline, janela de avaliação, catálogo elegível e K.

**Saída:** inventário de lacunas e ADR sobre eventos/atribuição.

### Etapa 1 — Monitoramento sem usuários (P0)

- [ ] Implementar `franchise_hit_at_k`, `genre_overlap_at_k`, `intra_list_diversity`, `catalog_coverage`.
- [ ] Instrumentar p50/p95/p99, error rate, fallback rate e neighbor availability.
- [ ] Registrar histórico com `catalog_version`, `model_version`, janela e amostra.
- [ ] Criar testes com casos extremos e dados sintéticos.
- [ ] Produzir relatório offline reproduzível.

**Aceite:** métricas reproduzíveis em amostra fixa, sem vazamento, com denominadores e versão registrados.

### Etapa 2 — Instrumentação de eventos (P1)

- [ ] Persistir eventos com idempotência e validação.
- [ ] Registrar listas servidas no backend e vincular exposições/cliques.
- [ ] Verificar funcionamento do IntersectionObserver e batch/sendBeacon.
- [ ] Criar agregações diárias por superfície, modelo e posição.
- [ ] Implementar CTR e Favorite Rate com janela de atribuição documentada.

**Aceite:** eventos duplicados não alteram contagens; métricas conciliam com dados brutos; perda/atraso são monitorados.

### Etapa 3 — Drift e dashboard (P1)

- [ ] Criar baseline de distribuições por versão de catálogo/modelo.
- [ ] Implementar Jensen–Shannon para gêneros e popularidade.
- [ ] Criar painel histórico e alertas com limites calibrados.
- [ ] Documentar runbook para diagnóstico de alertas.

**Aceite:** alertas de teste disparam com drift sintético conhecido e não provocam retreino automático.

### Etapa 4 — Avaliação com usuários e experimentos (P2)

- [ ] Definir rótulos e janelas temporais de relevância.
- [ ] Implementar NDCG@K, MAP@K e Recall@K com usuários, evitando tratar não exposição como negativo.
- [ ] Adicionar experiment_id e randomização estável para A/B.
- [ ] Monitorar métricas de guarda: latência, erros, fallback e diversidade.
- [ ] Comparar modelos com intervalos de confiança e tamanhos amostrais.

**Aceite:** avaliação documenta viés de exposição, incerteza estatística e critérios de decisão.

## 9. Estratégia de testes

Testar: lista vazia; K maior que número de itens; item repetido; consulta recomendando a si mesma; gênero ausente; vetores nulos ou de dimensão diferente; embeddings não normalizados; denominador zero; evento duplicado; evento atrasado; lista sem `request_id`; mudança de versão de modelo; ausência de baseline; empates e relevância graduada.

Testes de integração devem validar transações, índices únicos, agregações e idempotência. Testes de carga devem simular concorrência; a latência medida com um cliente sequencial não comprova capacidade sob carga.

## 10. Critérios de alertas (propostos, não metas comprovadas)

| Indicador | Regra inicial | Ação |
|---|---|---|
| p95 `/similar` | >200 ms por janela com amostra mínima | Inspecionar banco/cache/queries |
| Error Rate 5xx | Limite calibrado por rota e tráfego | Investigar logs e deploy |
| Fallback Rate | Aumento sustentado contra baseline | Verificar vizinhos e modelo ativo |
| Proxy Franchise Hit@10 | Queda além da variabilidade histórica | Reavaliar catálogo, rótulos e ranking |
| Catalog Coverage | Queda sustentada na amostra fixa | Investigar concentração e elegibilidade |
| Jensen–Shannon | Limite derivado da variabilidade histórica | Diagnosticar drift, sem retreino automático |
| CTR / Favorite Rate | Mudança com intervalo de confiança e volume adequado | Analisar posição, superfície e experimentos |

Evitar alertas com amostras pequenas. Registrar janela, mínimo de eventos e severidade. Preferir comparação contra baseline histórica e não thresholds arbitrários.

## 11. Estimativa relativa de esforço e riscos

| Frente | Esforço relativo | Risco principal |
|---|---|---|
| Métricas offline P0 | Baixo–médio | Rótulos proxy incompletos |
| Instrumentação de serving | Baixo–médio | Falta de histogramas/observabilidade |
| Persistência e atribuição de eventos | Médio–alto | Duplicidade, perda, identidade e privacidade |
| Drift e dashboard | Médio | Baseline inadequada e falsos alarmes |
| Métricas de relevância real/A-B | Alto | Viés de exposição e pouco tráfego |

**Viabilidade geral:** alta para P0 se as saídas do modelo, embeddings e catálogo estiverem disponíveis; condicional para métricas comportamentais até existir instrumentação confiável e tráfego real. Não é possível estimar horas ou custos sem inspecionar o repositório e a infraestrutura.

## 12. Decisões em aberto

1. O identificador `request_id` representa a chamada HTTP ou uma lista de recomendações? É estável em todos os eventos relacionados?
2. Qual a unidade de exposição: renderização, lista, sessão ou usuário?
3. Qual a janela de atribuição de clique e favorito? Como tratar múltiplas exposições?
4. Como construir um conjunto elegível e ground truth de relevância sem viés de exposição?
5. Como versionar catálogo, embeddings, encoder, ranking e fallback independentemente?
6. Onde executar jobs (cron, Airflow, worker ou outro orquestrador já existente)?
7. Qual a política de retenção, anonimização e exclusão dos eventos?
8. Qual o volume esperado para definir índices, agregações e eventual particionamento?

## 13. Checklist de revisão para Claude/Codex

Antes de implementar, produzir uma tabela **requisito → evidência no repositório → lacuna → alteração proposta → risco → teste**. Não afirmar que endpoints, tabelas ou métricas existem sem localizar código e migrations. Preferir mudanças incrementais, preservar o contrato da API e abrir ADR para extensões de eventos. Entregar PRs separados para métricas puras, persistência, jobs, dashboards e alertas.

## 14. Referências conceituais

- Manning, Raghavan & Schütze — *Introduction to Information Retrieval* (ranking e métricas).
- Shani & Gunawardana — *Evaluating Recommendation Systems* (avaliação offline e online).
- Documentação do scikit-learn: `ndcg_score` e similaridade de cosseno.
- Documentação do MLflow: experiment tracking e model registry.

---

**Recomendação:** iniciar pela auditoria e pelo P0, mantendo métricas proxy explicitamente separadas das métricas reais. A arquitetura deve permitir incorporar comportamento de usuários posteriormente sem reescrever o módulo de avaliação.
