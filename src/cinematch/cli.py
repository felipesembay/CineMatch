"""CLI do CineMatch.

Exemplos:
    cinematch db upgrade
    cinematch ingest tmdb --types movie tv --max-items 50
    cinematch ingest tmdb --resume <run_id>
    cinematch ingest status
    cinematch ingest purge-bronze --older-than-days 180
"""

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from cinematch.config import TmdbSettings, get_settings
from cinematch.logconfig import configure_logging

if TYPE_CHECKING:
    from alembic.config import Config

logger = logging.getLogger("cinematch.cli")

# Acima disso exige --yes (spec: não executar extrações grandes sem confirmação).
LARGE_RUN_THRESHOLD = 500
# Acima disso (ou --all) a varredura TVmaze exige --yes.
TVMAZE_LARGE_PAGES = 20
# TMDB §1.C: cache máximo de 6 meses.
TMDB_MAX_RETENTION_DAYS = 180


def _alembic_config() -> "Config":  # import tardio: CLI leve
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    ini = root / "alembic.ini"
    if not ini.exists():  # imagem Docker: alembic.ini em /app
        ini = Path.cwd() / "alembic.ini"
    return Config(str(ini))


def cmd_db_upgrade(args: argparse.Namespace) -> int:
    from alembic import command

    command.upgrade(_alembic_config(), args.revision)
    return 0


def cmd_ingest_tmdb(args: argparse.Namespace) -> int:
    from cinematch.db import get_engine
    from cinematch.ingestion.pipeline import TmdbIngestion
    from cinematch.ingestion.raw_store import LocalRawStore
    from cinematch.ingestion.tmdb.client import TmdbClient

    total = args.max_items * len(args.types)
    if args.resume is None and total > LARGE_RUN_THRESHOLD and not args.yes:
        print(
            f"Execução grande ({total} itens). Confirme limites/termos e rode com --yes.",
            file=sys.stderr,
        )
        return 2

    client = TmdbClient.from_settings(TmdbSettings())
    try:
        summary = TmdbIngestion(
            engine=get_engine(),
            client=client,
            store=LocalRawStore(get_settings().bronze_dir),
            batch_size=args.batch_size,
        ).run(
            item_types=args.types,
            max_items_per_type=args.max_items,
            resume_run_id=args.resume,
            skip_synced_within_days=args.skip_synced_within_days,
        )
    finally:
        client.close()
    print(
        json.dumps(
            {"run_id": summary.run_id, "status": summary.status, **summary.metrics},
            indent=2,
            default=str,
        )
    )
    return 0 if summary.status == "succeeded" else 1


def cmd_ingest_status(args: argparse.Namespace) -> int:
    from cinematch.db import get_engine
    from cinematch.ingestion import repository as repo

    with get_engine().connect() as conn:
        if args.run_id:
            run = repo.get_run(conn, args.run_id)
            runs = [run] if run else []
        else:
            runs = repo.latest_runs(conn, args.limit)
    print(json.dumps(runs, indent=2, default=str))
    return 0 if runs else 1


def cmd_ingest_tvmaze(args: argparse.Namespace) -> int:
    from cinematch.config import TvmazeSettings
    from cinematch.db import get_engine
    from cinematch.ingestion.raw_store import LocalRawStore
    from cinematch.ingestion.tvmaze.client import TvmazeClient
    from cinematch.ingestion.tvmaze.pipeline import TvmazeIngestion

    max_pages = None if args.all else args.max_pages
    large = max_pages is None or max_pages > TVMAZE_LARGE_PAGES
    if args.resume is None and large and not args.yes:
        print(
            "Varredura grande do índice TVmaze (~300+ páginas, ~6 min a 1 req/s). "
            "Rode com --yes para confirmar.",
            file=sys.stderr,
        )
        return 2
    client = TvmazeClient.from_settings(TvmazeSettings())
    try:
        summary = TvmazeIngestion(
            engine=get_engine(), client=client, store=LocalRawStore(get_settings().bronze_dir)
        ).run(max_pages=max_pages, start_page=args.start_page, resume_run_id=args.resume)
    finally:
        client.close()
    print(
        json.dumps(
            {"run_id": summary.run_id, "status": summary.status, **summary.metrics},
            indent=2,
            default=str,
        )
    )
    return 0 if summary.status == "succeeded" else 1


def cmd_link_tvmaze(_args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from cinematch.db import get_engine
    from cinematch.ingestion.tvmaze.pipeline import link_catalog

    with get_engine().begin() as conn:
        stats = link_catalog(conn, None, datetime.now(UTC))
    print(json.dumps(stats, indent=2))
    return 0


def cmd_reprocess_tmdb(_args: argparse.Namespace) -> int:
    from cinematch.db import get_engine
    from cinematch.ingestion.tmdb.backfill import reprocess_tmdb_bronze

    print(json.dumps(reprocess_tmdb_bronze(get_engine(), get_settings().bronze_dir), indent=2))
    return 0


def cmd_quality_check(args: argparse.Namespace) -> int:
    from cinematch.db import get_engine
    from cinematch.quality.checks import run_checks

    report = run_checks(
        get_engine(), None if args.skip_bronze else get_settings().bronze_dir, save=not args.no_save
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        for r in report.results:
            if args.all or r.status != "pass":
                print(f"{r.status.upper():5} {r.name:42} {r.value!s:>10}  {r.threshold or ''}")
        print(f"\nresultado: {report.status.upper()}  {report.summary['checks']}")
    return 1 if report.status == "fail" else 0


def cmd_features_embeddings(args: argparse.Namespace) -> int:
    from cinematch.db import get_engine
    from cinematch.features.embeddings import SentenceTransformerEncoder, compute_embeddings

    settings = get_settings()
    encoder = SentenceTransformerEncoder(settings.embedding_model, settings.embedding_revision)
    stats = compute_embeddings(
        get_engine(), encoder, args.batch_size, args.limit, text_version=args.text_version
    )
    print(json.dumps(stats.as_dict(), indent=2))
    return 0


def cmd_features_tfidf(_args: argparse.Namespace) -> int:
    from cinematch.db import get_engine
    from cinematch.features.tfidf import build_tfidf

    print(json.dumps(build_tfidf(get_engine(), get_settings().artifacts_dir), indent=2))
    return 0


def cmd_features_neighbors(args: argparse.Namespace) -> int:
    from sqlalchemy import text

    from cinematch.db import get_engine
    from cinematch.features.neighbors import embedding_neighbors, latest_model_version
    from cinematch.features.tfidf import load_latest_tfidf

    engine = get_engine()
    with engine.connect() as conn:
        mv = args.model_version or latest_model_version(conn)
        if args.method == "tfidf":
            pairs = load_latest_tfidf(engine).neighbors(args.item_id, args.k)
            label = "tfidf"
        else:
            if mv is None:
                print("erro: sem embeddings; rode `cinematch features embeddings`", file=sys.stderr)
                return 2
            pairs = embedding_neighbors(conn, args.item_id, mv, args.k)
            label = mv
        ids = [args.item_id, *[i for i, _ in pairs]]
        titles = dict(
            conn.execute(
                text(
                    "SELECT id, item_type || ' | ' || title || ' (' || "
                    "coalesce(release_year::text, '?') || ')' FROM catalog_items "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": ids},
            ).all()
        )
    print(f"[{label}] {titles.get(args.item_id)}")
    for i, score in pairs:
        print(f"  {score:.3f}  {titles.get(i)}")
    return 0


def cmd_ingest_tmdb_refresh(args: argparse.Namespace) -> int:
    from cinematch.db import get_engine
    from cinematch.ingestion.pipeline import TmdbIngestion
    from cinematch.ingestion.raw_store import LocalRawStore
    from cinematch.ingestion.tmdb.client import TmdbClient

    client = TmdbClient.from_settings(TmdbSettings())
    try:
        summary = TmdbIngestion(
            engine=get_engine(),
            client=client,
            store=LocalRawStore(get_settings().bronze_dir),
            batch_size=args.batch_size,
        ).refresh(older_than_days=args.older_than_days, max_items=args.max_items)
    finally:
        client.close()
    print(
        json.dumps(
            {"run_id": summary.run_id, "status": summary.status, **summary.metrics},
            indent=2,
            default=str,
        )
    )
    return 0 if summary.status == "succeeded" else 1


def cmd_catalog_expire(args: argparse.Namespace) -> int:
    from dataclasses import asdict

    from cinematch.catalog.retention import expire_tmdb_items
    from cinematch.db import get_engine

    result = expire_tmdb_items(get_engine(), args.older_than_days, apply=args.yes)
    print(json.dumps(asdict(result), indent=2, default=str))
    if not args.yes and result.candidates:
        print(
            f"simulação: {result.candidates} itens seriam removidos; use --yes para aplicar",
            file=sys.stderr,
        )
    return 0


def cmd_gold_snapshot(args: argparse.Namespace) -> int:
    from datetime import datetime

    from cinematch.data.gold import SnapshotRefusedError, create_snapshot
    from cinematch.db import get_engine

    try:
        manifest = create_snapshot(
            get_engine(),
            get_settings().gold_dir,
            name=args.name,
            as_of=datetime.fromisoformat(args.as_of) if args.as_of else None,
            embedding_model_version=args.embedding_model_version,
            allow_failing_quality=args.allow_failing_quality,
        )
    except SnapshotRefusedError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2, default=str))
    return 0


def cmd_gold_list(args: argparse.Namespace) -> int:
    from sqlalchemy import desc, select

    from cinematch.data.schema import training_datasets as td
    from cinematch.db import get_engine

    with get_engine().connect() as conn:
        rows = conn.execute(
            select(td.c.snapshot_id, td.c.as_of, td.c.uri, td.c.manifest["rows_by_type"])
            .order_by(desc(td.c.created_at))
            .limit(args.limit)
        ).all()
    print(json.dumps([dict(r._mapping) for r in rows], indent=2, default=str))
    return 0


def cmd_recs_build(args: argparse.Namespace) -> int:
    from cinematch.db import get_engine
    from cinematch.serving.build import build_and_store

    result = build_and_store(
        get_engine(), args.method, k=args.k, activate=args.activate, log_mlflow=args.mlflow
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_recs_activate(args: argparse.Namespace) -> int:
    from cinematch.db import get_engine
    from cinematch.serving.neighbors import activate_set

    try:
        activate_set(get_engine(), args.model_version)
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"active": args.model_version}))
    return 0


def cmd_recs_list(args: argparse.Namespace) -> int:
    from cinematch.db import get_engine
    from cinematch.serving.neighbors import list_sets

    print(json.dumps(list_sets(get_engine()), indent=2, default=str))
    return 0


def cmd_evaluate_latency(args: argparse.Namespace) -> int:
    from sqlalchemy import text

    from cinematch.db import get_engine
    from cinematch.evaluation.latency import run_latency

    with get_engine().connect() as conn:
        ids = list(conn.execute(text("SELECT id FROM catalog_items ORDER BY id")).scalars())
    report = run_latency(args.base_url, ids, requests=args.requests)
    out = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(out + "\n")
    print(out)
    return 0


def cmd_evaluate_similarity(args: argparse.Namespace) -> int:
    from cinematch.db import get_engine
    from cinematch.features.tfidf import load_latest_tfidf
    from cinematch.similarity.benchmark import run_benchmark
    from cinematch.similarity.data import load_similarity_data, stored_model_versions
    from cinematch.similarity.rerank import SUPERVISED

    engine = get_engine()
    versions = stored_model_versions(engine)
    if args.dense not in versions:
        print(f"embedding '{args.dense}' ausente; disponíveis: {sorted(versions)}", file=sys.stderr)
        return 2
    data = load_similarity_data(engine, versions, load_latest_tfidf(engine))
    report = run_benchmark(
        data,
        dense_rep=args.dense,
        k=args.k,
        depth=args.depth,
        supervised=() if args.no_supervised else SUPERVISED,
        log_mlflow=args.mlflow,
        experiment=args.experiment,
    )
    report.pop("neighbors")
    out = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(out + "\n")
    print(out)
    return 0


def cmd_evaluate_content(args: argparse.Namespace) -> int:
    from sqlalchemy import text

    from cinematch.db import get_engine
    from cinematch.evaluation.content_sanity import compare_methods
    from cinematch.features.tfidf import load_latest_tfidf

    engine = get_engine()
    versions = args.model_version
    if not versions:
        with engine.connect() as conn:
            versions = (
                conn.execute(text("SELECT DISTINCT model_version FROM item_embeddings ORDER BY 1"))
                .scalars()
                .all()
            )
    report = compare_methods(
        engine,
        list(versions),
        tfidf_loader=lambda: load_latest_tfidf(engine),
        k=args.k,
    )
    out = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(out + "\n")
    print(out)
    return 0


def cmd_purge_bronze(args: argparse.Namespace) -> int:
    from cinematch.ingestion.raw_store import purge_older_than

    removed = purge_older_than(get_settings().bronze_dir, args.older_than_days, source=args.source)
    print(json.dumps({"removed_partitions": [str(p) for p in removed]}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cinematch")
    sub = parser.add_subparsers(dest="command", required=True)

    db = sub.add_parser("db", help="migrações").add_subparsers(dest="db_command", required=True)
    up = db.add_parser("upgrade", help="aplica migrações Alembic")
    up.add_argument("revision", nargs="?", default="head")
    up.set_defaults(func=cmd_db_upgrade)

    ingest = sub.add_parser("ingest", help="ingestão de catálogo").add_subparsers(
        dest="ingest_command", required=True
    )
    tmdb = ingest.add_parser("tmdb", help="coleta TMDB (discover + detalhes)")
    tmdb.add_argument("--types", nargs="+", choices=["movie", "tv"], default=["movie", "tv"])
    tmdb.add_argument("--max-items", type=int, default=50, help="máximo por tipo (padrão 50)")
    tmdb.add_argument("--batch-size", type=int, default=50)
    tmdb.add_argument("--resume", metavar="RUN_ID", help="retoma um run interrompido")
    tmdb.add_argument(
        "--skip-synced-within-days",
        type=int,
        metavar="D",
        help="pula itens sincronizados nos últimos D dias; --max-items passa a contar só novos",
    )
    tmdb.add_argument("--yes", action="store_true", help=f"confirma > {LARGE_RUN_THRESHOLD} itens")
    tmdb.set_defaults(func=cmd_ingest_tmdb)

    status = ingest.add_parser("status", help="mostra runs recentes")
    status.add_argument("run_id", nargs="?")
    status.add_argument("--limit", type=int, default=5)
    status.set_defaults(func=cmd_ingest_status)

    purge = ingest.add_parser("purge-bronze", help="remove partições Bronze antigas")
    purge.add_argument(
        "--older-than-days",
        type=int,
        default=TMDB_MAX_RETENTION_DAYS,
        choices=range(0, TMDB_MAX_RETENTION_DAYS + 1),
        metavar=f"[0-{TMDB_MAX_RETENTION_DAYS}]",
    )
    purge.add_argument("--source", choices=["tmdb", "tvmaze"], default="tmdb")
    purge.set_defaults(func=cmd_purge_bronze)

    refresh = ingest.add_parser(
        "tmdb-refresh", help="rebusca itens TMDB antigos (termos: cache máximo de 6 meses)"
    )
    refresh.add_argument("--older-than-days", type=int, default=150)
    refresh.add_argument("--max-items", type=int, help="limite de itens por execução")
    refresh.add_argument("--batch-size", type=int, default=50)
    refresh.set_defaults(func=cmd_ingest_tmdb_refresh)

    tvmaze = ingest.add_parser("tvmaze", help="varre o índice de shows do TVmaze")
    pages = tvmaze.add_mutually_exclusive_group()
    pages.add_argument("--max-pages", type=int, default=2, help="páginas de 250 shows (padrão 2)")
    pages.add_argument("--all", action="store_true", help="varre até o fim do índice")
    tvmaze.add_argument("--start-page", type=int, default=0)
    tvmaze.add_argument("--resume", metavar="RUN_ID")
    tvmaze.add_argument(
        "--yes", action="store_true", help=f"confirma --all ou > {TVMAZE_LARGE_PAGES} páginas"
    )
    tvmaze.set_defaults(func=cmd_ingest_tvmaze)

    link = sub.add_parser("link", help="resolução de entidades entre fontes").add_subparsers(
        dest="link_command", required=True
    )
    link.add_parser(
        "tvmaze", help="liga shows TVmaze a séries do catálogo por IMDb/TVDB"
    ).set_defaults(func=cmd_link_tvmaze)

    catalog = sub.add_parser("catalog", help="manutenção do catálogo").add_subparsers(
        dest="catalog_command", required=True
    )
    expire = catalog.add_parser(
        "expire", help="remove itens TMDB sem sincronização (simulação por padrão)"
    )
    expire.add_argument("--older-than-days", type=int, default=TMDB_MAX_RETENTION_DAYS)
    expire.add_argument("--yes", action="store_true", help="aplica a remoção (irreversível)")
    expire.set_defaults(func=cmd_catalog_expire)

    gold = sub.add_parser("gold", help="snapshots Gold versionados").add_subparsers(
        dest="gold_command", required=True
    )
    snap = gold.add_parser("snapshot", help="gera snapshot Parquet imutável (extra ml)")
    snap.add_argument("--name", default="catalog_items")
    snap.add_argument("--as-of", help="ISO 8601 com fuso; padrão: agora")
    snap.add_argument("--embedding-model-version", help="padrão: a mais recente")
    snap.add_argument("--allow-failing-quality", action="store_true")
    snap.set_defaults(func=cmd_gold_snapshot)
    gl = gold.add_parser("list", help="snapshots registrados")
    gl.add_argument("--limit", type=int, default=10)
    gl.set_defaults(func=cmd_gold_list)

    evaluate = sub.add_parser("evaluate", help="avaliação").add_subparsers(
        dest="evaluate_command", required=True
    )
    ev = evaluate.add_parser(
        "content-sanity", help="proxies de sanidade TF-IDF vs embeddings (sem dados de usuário)"
    )
    ev.add_argument("--model-version", action="append", help="repetível; padrão: todas")
    ev.add_argument("-k", type=int, default=10)
    ev.add_argument("--out", help="grava o relatório JSON neste caminho")
    ev.set_defaults(func=cmd_evaluate_content)
    sm = evaluate.add_parser(
        "similarity-models",
        help="cosseno, RRF, Rocchio e rerankers (LR/SGD/LightGBM) com rótulo fraco",
    )
    sm.add_argument("--dense", default="emb_v2", help="embedding para candidatos/RRF/Rocchio")
    sm.add_argument("-k", type=int, default=10)
    sm.add_argument("--depth", type=int, default=50, help="candidatos por representação")
    sm.add_argument("--no-supervised", action="store_true", help="pula os rerankers")
    sm.add_argument("--mlflow", action="store_true", help="registra os runs no MLflow")
    sm.add_argument("--experiment", default="similarity-item2item")
    sm.add_argument("--out", help="grava o relatório JSON neste caminho")
    sm.set_defaults(func=cmd_evaluate_similarity)
    lat = evaluate.add_parser("api-latency", help="p50/p95/p99 da API em execução")
    lat.add_argument("--base-url", default="http://127.0.0.1:8010")
    lat.add_argument("--requests", type=int, default=300, help="por endpoint")
    lat.add_argument("--out", help="grava o relatório JSON neste caminho")
    lat.set_defaults(func=cmd_evaluate_latency)

    recs = sub.add_parser("recs", help="conjuntos de vizinhos servidos pela API").add_subparsers(
        dest="recs_command", required=True
    )
    rb = recs.add_parser("build-similar", help="gera vizinhos em batch (extra ml)")
    rb.add_argument("--method", choices=["tfidf", "rerank-logreg"], default="tfidf")
    rb.add_argument("-k", type=int, default=50, help="vizinhos guardados por item")
    rb.add_argument("--activate", action="store_true", help="passa a servir este conjunto")
    rb.add_argument("--mlflow", action="store_true", help="registra o build no MLflow")
    rb.set_defaults(func=cmd_recs_build)
    ra = recs.add_parser("activate", help="troca o conjunto servido (rollback incluso)")
    ra.add_argument("model_version")
    ra.set_defaults(func=cmd_recs_activate)
    rl = recs.add_parser("list", help="conjuntos gravados")
    rl.set_defaults(func=cmd_recs_list)

    quality = sub.add_parser("quality", help="qualidade de dados").add_subparsers(
        dest="quality_command", required=True
    )
    qc = quality.add_parser("check", help="roda os checks e grava o resultado")
    qc.add_argument("--json", action="store_true", help="saída JSON completa")
    qc.add_argument("--all", action="store_true", help="lista também os checks que passaram")
    qc.add_argument("--no-save", action="store_true", help="não grava em data_quality_runs")
    qc.add_argument("--skip-bronze", action="store_true", help="não amostra arquivos Bronze")
    qc.set_defaults(func=cmd_quality_check)

    features = sub.add_parser("features", help="features de conteúdo").add_subparsers(
        dest="features_command", required=True
    )
    emb = features.add_parser("embeddings", help="embeddings incrementais em pgvector (extra ml)")
    emb.add_argument("--batch-size", type=int, default=64)
    emb.add_argument("--limit", type=int, help="processa só os N primeiros itens (teste)")
    emb.add_argument(
        "--text-version", default="emb_text_v1", choices=["emb_text_v1", "emb_text_v2"]
    )
    emb.set_defaults(func=cmd_features_embeddings)
    features.add_parser("tfidf", help="gera artefato TF-IDF versionado (extra ml)").set_defaults(
        func=cmd_features_tfidf
    )
    nb = features.add_parser("neighbors", help="vizinhos de um item (checagem qualitativa)")
    nb.add_argument("item_id", type=int)
    nb.add_argument("--method", choices=["embedding", "tfidf"], default="embedding")
    nb.add_argument("-k", type=int, default=10)
    nb.add_argument("--model-version", help="padrão: a mais recente")
    nb.set_defaults(func=cmd_features_neighbors)

    reprocess = sub.add_parser("reprocess", help="re-normaliza a partir do Bronze (sem API)")
    rp = reprocess.add_subparsers(dest="reprocess_command", required=True)
    rp.add_parser(
        "tmdb", help="upsert completo do registro Bronze mais recente de cada item TMDB"
    ).set_defaults(func=cmd_reprocess_tmdb)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    args = build_parser().parse_args(argv)
    from cinematch.ingestion.http import AuthError
    from cinematch.ingestion.pipeline import IngestionError
    from cinematch.ingestion.repository import ConcurrentRunError

    try:
        return int(args.func(args))
    except (AuthError, IngestionError, ConcurrentRunError) as exc:
        # Erros esperados: mensagem curta, sem traceback (nunca contém o token).
        print(f"erro: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
