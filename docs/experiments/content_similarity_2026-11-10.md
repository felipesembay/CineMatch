# Experimento: similaridade de conteúdo (TF-IDF × embeddings v1 × embeddings v2)

**Data:** 2026-11-10 · **Comando:** `cinematch evaluate content-sanity` · **Resultado bruto:** [content_sanity_2026-11-10.json](content_sanity_2026-11-10.json)

> **Escopo:** testes de sanidade com rótulos fracos, sem dados de usuários. Isto **não** mede relevância para pessoas e **não** é NDCG (spec §8).

## Configuração

- **Itens:** 8.000 (5.500 filmes e 2.500 séries), o mesmo conjunto para todos os métodos. Top-10 por cosseno, excluindo o próprio item; empates desfeitos pelo `item_id`.
- **Métodos:**
  - **TF-IDF:** `tfidf_text_v1|sklearn-1.9.1|p-3b78591d`. Título + sinopse + tokens de gênero, keywords e pessoas.
  - **Emb v1:** `paraphrase-multilingual-MiniLM-L12-v2@e8f8c211226b|emb_text_v1`. Título + gêneros + keywords + sinopse.
  - **Emb v2:** mesmo modelo, `emb_text_v2`. **Sem o título.**
- **Hipótese testada:** em v1, o título domina o vetor (observado em 3 exemplos qualitativos).

## Resultados (k = 10)

| Métrica | TF-IDF | Emb v1 | Emb v2 |
|---|---|---|---|
| Recall de coleção@10 (1.238 filmes; IC 95% por bootstrap) | **0,970** [0,963–0,977] | 0,882 [0,868–0,897] | 0,605 [0,582–0,629] |
| Sobreposição de gênero@10 | 0,806 | 0,846 | **0,939** |
| Sobreposição de palavras do título@10 (menor = menos vazamento) | 0,110 | 0,164 | **0,050** |
| Mesmo tipo@10 | 0,804 | 0,760 | 0,818 |
| Cobertura do catálogo@10 | **0,999** | 0,945 | 0,899 |

## Leitura

1. **A hipótese se confirma em parte.** Remover o título derruba a sobreposição de título de 0,164 para 0,050, então o título de fato pesava em v1. Mas v2 perde muito no recall de coleção (0,882 → 0,605), porque o título também carrega o sinal de franquia.
2. **TF-IDF lidera o proxy de franquia** (0,970) e a cobertura. Viés conhecido: membros de uma coleção compartilham keywords (ex.: `kw_sequel`), elenco e diretor como tokens exatos, e o rótulo "coleção" favorece correspondências lexicais e de metadados.
3. **Emb v2 lidera a coerência temática:** gênero 0,939. Qualitativamente, Breaking Bad → El Camino, Shot Caller, El Infierno, Power, Better Call Saul (v1 trazia "Breakdown" e "Point Break").
4. **Os métodos capturam noções diferentes de "similar":** franquia e pessoas (TF-IDF) contra tema e gênero (emb v2). Nenhum vence nos dois proxies, e nenhum proxy equivale à preferência de usuários.
5. Cobertura menor nos embeddings (0,90–0,95) sugere *hubness*: alguns itens aparecem em muitos top-10. A monitorar na Fase 3.

## Decisão e próximos passos (ver ADR-016)

- **Fase 3:**
  - Baseline de `/movies/{id}/similar` = **TF-IDF** (determinístico, barato, melhor no proxy de franquia).
  - Candidato a comparar = **fusão TF-IDF + emb v2** (ex.: Reciprocal Rank Fusion), avaliado com os mesmos proxies e revisão qualitativa.
- **Escolha definitiva** só com dados de interação (Fase 4/5): CTR e favoritos por método, com atribuição de exposição.
- **Emb v1 deixa de ser candidato principal:** dominado por v2 na coerência temática e pelo TF-IDF no proxy de franquia.

## Limitações

- O rótulo de coleção só existe para filmes (1.238 consultas). Séries não têm proxy de franquia.
- Os proxies não medem novidade nem serendipidade.
- Itens sem sinopse (≈ 2%) têm texto pobre em todos os métodos.
