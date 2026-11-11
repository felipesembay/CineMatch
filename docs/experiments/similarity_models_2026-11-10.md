# Experimento: modelos de similaridade item-item

**Data:** 2026-11-10 · **Comando:** `cinematch evaluate similarity-models --mlflow` · **Notebook:** [models/similarity_models.ipynb](../../models/similarity_models.ipynb) · **Resultado bruto:** [similarity_models_2026-11-10.json](similarity_models_2026-11-10.json) · **MLflow:** experimento `similarity-item2item` (15 runs) em `http://127.0.0.1:5010`

> **Escopo:** sem interações de usuários. Os modelos supervisionados aprendem o **rótulo fraco** "mesma coleção TMDB". Todas as métricas são **proxies**: não medem relevância para pessoas e não são NDCG de usuários (spec §8).

## Configuração

- **Itens:** 8.000 (5.500 filmes e 2.500 séries), fingerprint `338b482788c7906e`. Mesmo conjunto para todos os métodos.
- **Representações:**
  - TF-IDF `tfidf_text_v1|sklearn-1.9.1|p-3b78591d`.
  - `paraphrase-multilingual-MiniLM-L12-v2@e8f8c211226b` com `emb_text_v1` (com título) e `emb_text_v2` (sem título).
- **Métodos:**
  - **Sentence Transformers + cosseno:** emb v1 e emb v2. **TF-IDF + cosseno** como baseline.
  - **RRF:** Reciprocal Rank Fusion de TF-IDF e emb v2 (c = 60, profundidade 100).
  - **Rocchio PRF:** `q' = q + β·média(top-m)`, com β = 0,75 e m = 5 no CLI. A grade no notebook (β ∈ {0,5; 0,75}, m ∈ {3, 5, 10}) varia o recall em menos de 0,01.
  - **Rocchio de perfil:** tarefa à parte, descrita abaixo.
  - **Rerankers:** LogisticRegression, SGDClassifier (log-loss) e LightGBM LambdaRank.
    - Candidatos: top-50 TF-IDF ∪ top-50 emb v2 (735.007 pares; teto de recall de 0,994).
    - 14 features, sem título.
    - GroupKFold(5) por coleção, com predição out-of-fold.
- **k = 10.** Recall de coleção calculado em 1.238 filmes cuja coleção tem 2 ou mais membros no catálogo.

## Resultados item → item

| Método | Recall coleção@10 | nDCG coleção@10 | Gênero@10 | Título@10 ↓ | Mesmo tipo@10 | Cobertura@10 | Hub 1%@10 ↓ |
|---|---|---|---|---|---|---|---|
| TF-IDF cosseno | 0,970 | 0,927 | 0,806 | 0,110 | 0,804 | **0,999** | 0,067 |
| Emb v1 cosseno | 0,882 | 0,814 | 0,846 | 0,164 | 0,760 | 0,945 | 0,087 |
| Emb v2 cosseno | 0,605 | 0,527 | 0,939 | **0,050** | 0,818 | 0,899 | 0,093 |
| RRF (TF-IDF + emb v2) | 0,846 | 0,755 | 0,932 | 0,083 | 0,854 | 0,964 | 0,063 |
| Rocchio PRF (TF-IDF) | 0,973 | 0,923 | 0,832 | 0,110 | 0,811 | 0,993 | 0,104 |
| Rocchio PRF (emb v2) | 0,577 | 0,485 | 0,942 | 0,049 | 0,817 | 0,825 | 0,141 |
| Reranker LogisticRegression | 0,985 | 0,961 | 0,967 | 0,099 | 0,999 | 0,982 | 0,051 |
| Reranker SGD | 0,985 | 0,960 | **0,969** | 0,099 | 0,998 | 0,982 | **0,049** |
| Reranker LightGBM | **0,986** | **0,970** | 0,964 | 0,112 | 0,920 | 0,984 | 0,055 |
| LightGBM sem metadados (notebook) | 0,975 | 0,934 | 0,882 | 0,132 | 0,835 | 0,990 | 0,061 |

- **Recall por fold:** LogisticRegression de 0,978 a 0,994; LightGBM de 0,979 a 0,994.
- **Tempo:** cada método não supervisionado roda em 3 a 8 s; cada reranker em 3 a 21 s (CPU).

## Resultados por perfil (2 itens curtidos de uma franquia → achar o restante)

168 coleções com 3 ou mais membros. IC 95% por bootstrap.

| Estratégia | TF-IDF | Emb v2 |
|---|---|---|
| Só a 1ª seed | 0,957 [0,931–0,979] | 0,529 [0,461–0,599] |
| MaxSim (máximo por seed) | 0,978 [0,958–0,992] | 0,620 [0,555–0,688] |
| **Rocchio** (média dos vetores) | **0,985** [0,969–0,996] | **0,637** [0,575–0,706] |

## Leitura

1. **Rerankers supervisionados lideram todos os proxies ao mesmo tempo:** recall de coleção (0,985 contra 0,970 do TF-IDF), gênero (0,96–0,97 contra 0,81) e hubness (menor). O recall de coleção é **circular**: é o rótulo de treino, embora em coleções separadas por GroupKFold. O ganho em gênero e hubness, que não foram treinados, é o sinal mais informativo.
2. **Parte do ganho em gênero vem de metadados:** sem metadados, o LightGBM cai de 0,964 para 0,882 em gênero. Os modelos lineares dão o maior peso a `same_type` e `same_language`.
3. **Viés por tipo:** só filmes têm coleção, então o modelo aprende "mesmo tipo" quase como regra (LogisticRegression: 99,9% dos vizinhos do mesmo tipo). Séries passam a receber só séries. Isso pode ser desejável, mas é efeito do rótulo, não preferência medida.
4. **LightGBM ≈ modelos lineares:** diferenças de 0,001 a 0,01 em recall, dentro do IC. A LogisticRegression é mais simples, explicável e barata (3,8 s).
5. **Rocchio PRF quase não muda o item → item** (±0,02) e piora a hubness com emb v2 (0,093 → 0,141). Não compensa.
6. **Rocchio de perfil vence a seed única e o MaxSim** nas duas representações. É o método natural para recomendar a partir de vários itens curtidos (onboarding da Fase 4) e ainda aceita itens não curtidos (γ).
7. **RRF** fica no meio-termo: gênero 0,932 e recall 0,846. Supera emb v2 em franquia e TF-IDF em tema, mas os rerankers superam o RRF nos dois.
8. **Qualitativo** (notebook): os rerankers trazem vizinhos coerentes. Interstellar → 2001, The Martian, A.I., Gravity. Breaking Bad → El Camino, Narcos, Better Call Saul. O TF-IDF puro traz ruído temático (Interstellar → Idiocracy).

## Decisão (ver ADR-017)

- **Baseline do `/similar` (Fase 3):** continua TF-IDF (ADR-016). É determinístico, sem treino e sem dependência de rótulo.
- **Challenger principal:** reranker LogisticRegression sobre candidatos TF-IDF ∪ emb v2. LightGBM fica como alternativa; só vale a complexidade se houver ganho com dados de uso.
- **Rocchio de perfil:** adotado para recomendação a partir de vários itens (Fase 4).
- **Descartados como candidatos principais:** Rocchio PRF item → item e emb v1.
- **XGBoost** não testado: LightGBM cobre gradient boosting, e o wheel Linux do XGBoost traz CUDA (~300 MB). O LightGBM já empata com os lineares.
- **Escolha definitiva** só com dados de interação (Fase 4/5), com atribuição de exposição.

## Limitações

- O rótulo de coleção existe só para filmes (1.238 consultas). Séries são avaliadas apenas por gênero, título, cobertura e hubness.
- Viés do rótulo: favorece franquias, e um `/similar` só de sequências seria pobre. Mitigação futura: limite de itens por coleção (diversificação) e avaliação online.
- Os atributos dos itens são do momento atual, não point-in-time (ver `data/gold.py`).
- `git_commit` dos runs = `none`, porque o repositório ainda não tem commits.
