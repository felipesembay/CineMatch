# Fontes de dados, termos e atribuição

Consulta realizada em **2026-11-10**. Reverificar os termos antes de cada expansão de coleta e a cada 6 meses.

## TMDB — The Movie Database

| Item | Situação |
|---|---|
| Documento | [API Terms of Use](https://www.themoviedb.org/api-terms-of-use), última revisão **2023-10-20** |
| Autenticação | Bearer token (API Read Access Token): `Authorization: Bearer <token>` ([docs](https://developer.themoviedb.org/docs/authentication-application)). No projeto, vem de `TMDB_API_TOKEN` no `.env` |
| Rate limit | Não há número fixo publicado. A [documentação](https://developer.themoviedb.org/docs/rate-limiting) fala em algo "na faixa de ~40 req/s", sujeito a mudança, e pede que 429 seja respeitado. O CineMatch usa **4 req/s** (`TMDB_REQUESTS_PER_SECOND`), respeita `Retry-After` e faz backoff exponencial |
| Uso comercial | Exige acordo escrito (§2.A). O CineMatch é **não comercial** |
| Atribuição | Logo TMDB menos proeminente que a marca própria, mais o aviso: *"This product uses the TMDB API but is not endorsed or certified by TMDB."* A página de termos cita "uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB" |
| Cache | **Máximo de 6 meses** (§1.C). Ao fim da licença, apagar todo o conteúdo (§1.D) |
| ML/IA | **Restrito.** §1.A e §1.C proíbem usar as APIs ou o conteúdo TMDB "in connection with, including for training, a machine learning (ML) or artificial intelligence (AI) based Application". §2.A classifica treinar/validar ML e montar datasets para isso como uso comercial |

### Risco aceito (ver ADR-008)

O CineMatch é um recomendador, portanto uma aplicação de ML. Ao usar TMDB como fonte principal, ele **contraria os §1.A/§1.C**. O responsável pelo projeto foi informado e, em 2026-11-10, decidiu manter o TMDB. Mitigações implementadas ou obrigatórias:

1. Uso estritamente não comercial, sem receita e sem redistribuir datasets que contenham conteúdo TMDB.
2. Retenção do Bronze ≤ 180 dias: `cinematch ingest purge-bronze` (o limite máximo do CLI é 180).
3. O catálogo no Postgres é renovado por re-coleta. `item_external_ids.last_synced_at` permite achar itens com mais de 180 dias sem sincronização. A rotina de expurgo/refresh entra na Fase 2.
4. Atribuição e logo no frontend (Fase 4) e no README.
5. Adaptador TMDB isolado (`cinematch.ingestion.tmdb`). Trocar de fonte não altera o schema nem o pipeline.
6. Reavaliar caso o projeto vire público ou comercial, ou caso o TMDB responda a um pedido de permissão.

### O que é coletado

- Descoberta: `GET /discover/{movie|tv}?sort_by=popularity.desc&include_adult=false`, até 500 páginas.
- Detalhes: `GET /{movie|tv}/{id}?append_to_response=keywords,credits,external_ids`. Keywords e créditos ficam no Bronze para normalização futura (Fase 2).
- `vote_average`/`vote_count` são **agregados externos do TMDB**, não avaliações de usuários do CineMatch.

## TVmaze (implementado)

| Item | Situação |
|---|---|
| Documento | [tvmaze.com/api](https://www.tvmaze.com/api) |
| Licença | **CC BY-SA 4.0**, qualquer finalidade com atribuição e ShareAlike |
| Atribuição | Link para o TVmaze dentro da aplicação |
| Rate limit | Pelo menos 20 chamadas a cada 10 s por IP. 429 pede retry após pausa |
| ML | Os termos não mencionam ML/IA |
| Coleta | `GET /shows?page=N` (índice paginado por ID, 250 shows/página, fim = 404). Cada página já traz o show completo com `externals` (IMDb, TheTVDB, TVRage). 1 req/s (`TVMAZE_REQUESTS_PER_SECOND`). User-Agent `CineMatch/0.1 (non-commercial research project)` |
| Cache | Sem limite de retenção nos termos. `purge-bronze` só atua em `--source tvmaze` quando pedido explicitamente |
| ShareAlike | Dados derivados do TVmaze (ex.: sinopses, embeddings feitos a partir delas), se forem distribuídos, devem sair sob CC BY-SA 4.0 com atribuição |

### Resolução de entidades TVmaze ↔ catálogo (ADR-011)

- Um show só é ligado a uma série do catálogo quando o IMDb ID e/ou o TheTVDB ID do show coincidem com os IDs que o próprio TMDB declarou para aquela série (crosswalk `match_method='tmdb_external_ids'`).
- IDs que apontam para itens diferentes, ou dois shows apontando para o mesmo item, contam como **ambíguos** e não são ligados.
- Não há correspondência por título ou ano.
- Shows que só existem no TVmaze ficam em `tvmaze_shows`, mas não viram itens do catálogo.

## Proveniência

Cada registro Bronze traz `source`, `entity`, `external_id`, `endpoint`, `request_params` (sem token), `status_code`, `fetched_at`, `run_id` e `license_notes`. No Postgres, `item_external_ids` guarda `source`, `external_type`, `external_id`, `first_seen_at`, `last_synced_at`, `last_run_id` e `raw_uri` (arquivo Bronze de origem).
