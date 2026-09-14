"""M43 — Idempotent Vector Embedding Backfill CLI Script for Project AURA.

Scans knowledge document chunks, user memories, and experiences with missing vector
embeddings and populates them using the configured embedding provider.
Supports batching, resume-on-failure, and dry-run mode.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any

from app.config import settings
from core.repositories.factory import create_repository_container
from providers.embedding.factory import create_embedding_provider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("aura.backfill_embeddings")


def backfill_knowledge_chunks(
    repo_container: Any,
    embedding_provider: Any,
    batch_size: int = 32,
    dry_run: bool = False,
) -> int:
    """Backfill missing embeddings for document chunks."""
    logger.info("Scanning for document chunks with missing embeddings...")
    updated_count = 0

    if not repo_container.is_postgres or repo_container.db_pool is None:
        logger.info("PostgreSQL is not active; skipping document chunk backfill.")
        return 0

    pool = repo_container.db_pool
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, doc_id, user_id, content
                FROM document_chunks
                WHERE embedding IS NULL
                ORDER BY created_at ASC;
                """
            )
            rows = cur.fetchall()

    total_chunks = len(rows)
    logger.info(f"Found {total_chunks} document chunks needing embeddings.")
    if total_chunks == 0 or dry_run:
        return total_chunks

    for i in range(0, total_chunks, batch_size):
        batch = rows[i : i + batch_size]
        texts = [r["content"] for r in batch]
        try:
            vectors = embedding_provider.embed_batch(texts)
            with pool.transaction() as conn:
                with conn.cursor() as cur:
                    for row, vec in zip(batch, vectors):
                        vec_str = "[" + ",".join(str(float(x)) for x in vec) + "]"
                        cur.execute(
                            "UPDATE document_chunks SET embedding = %s::vector WHERE id = %s;",
                            (vec_str, row["id"]),
                        )
                        updated_count += 1
            logger.info(f"Processed chunks {min(i + batch_size, total_chunks)}/{total_chunks}")
        except Exception as e:
            logger.error(f"Error embedding batch starting at index {i}: {e}")

    return updated_count


def backfill_memories(
    repo_container: Any,
    embedding_provider: Any,
    user_id: str | None = None,
    batch_size: int = 32,
    dry_run: bool = False,
) -> int:
    """Backfill missing embeddings for user memories."""
    logger.info("Scanning for user memories with missing embeddings...")
    updated_count = 0

    if not repo_container.is_postgres or repo_container.db_pool is None:
        logger.info("PostgreSQL is not active; skipping memory backfill.")
        return 0

    pool = repo_container.db_pool
    query_sql = "SELECT id, user_id, content FROM user_memories WHERE embedding IS NULL"
    params: list[Any] = []
    if user_id:
        query_sql += " AND user_id = %s"
        params.append(user_id)
    query_sql += " ORDER BY created_at ASC;"

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query_sql, tuple(params))
            rows = cur.fetchall()

    total_memories = len(rows)
    logger.info(f"Found {total_memories} memories needing embeddings.")
    if total_memories == 0 or dry_run:
        return total_memories

    for i in range(0, total_memories, batch_size):
        batch = rows[i : i + batch_size]
        texts = [r["content"] for r in batch]
        try:
            vectors = embedding_provider.embed_batch(texts)
            with pool.transaction() as conn:
                with conn.cursor() as cur:
                    for row, vec in zip(batch, vectors):
                        vec_str = "[" + ",".join(str(float(x)) for x in vec) + "]"
                        cur.execute(
                            "UPDATE user_memories SET embedding = %s::vector WHERE id = %s AND user_id = %s;",
                            (vec_str, row["id"], row["user_id"]),
                        )
                        updated_count += 1
            logger.info(f"Processed memories {min(i + batch_size, total_memories)}/{total_memories}")
        except Exception as e:
            logger.error(f"Error embedding memory batch at index {i}: {e}")

    return updated_count


def backfill_experiences(
    repo_container: Any,
    embedding_provider: Any,
    user_id: str | None = None,
    batch_size: int = 32,
    dry_run: bool = False,
) -> int:
    """Backfill missing embeddings for episodic experiences."""
    logger.info("Scanning for experiences with missing embeddings...")
    updated_count = 0

    if not repo_container.is_postgres or repo_container.db_pool is None:
        logger.info("PostgreSQL is not active; skipping experience backfill.")
        return 0

    pool = repo_container.db_pool
    query_sql = "SELECT id, user_id, task_description, plan_summary FROM user_experiences WHERE embedding IS NULL"
    params: list[Any] = []
    if user_id:
        query_sql += " AND user_id = %s"
        params.append(user_id)
    query_sql += " ORDER BY created_at ASC;"

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query_sql, tuple(params))
            rows = cur.fetchall()

    total_exp = len(rows)
    logger.info(f"Found {total_exp} experiences needing embeddings.")
    if total_exp == 0 or dry_run:
        return total_exp

    for i in range(0, total_exp, batch_size):
        batch = rows[i : i + batch_size]
        texts = [f"{r['task_description']} {r['plan_summary']}" for r in batch]
        try:
            vectors = embedding_provider.embed_batch(texts)
            with pool.transaction() as conn:
                with conn.cursor() as cur:
                    for row, vec in zip(batch, vectors):
                        vec_str = "[" + ",".join(str(float(x)) for x in vec) + "]"
                        cur.execute(
                            "UPDATE user_experiences SET embedding = %s::vector WHERE id = %s AND user_id = %s;",
                            (vec_str, row["id"], row["user_id"]),
                        )
                        updated_count += 1
            logger.info(f"Processed experiences {min(i + batch_size, total_exp)}/{total_exp}")
        except Exception as e:
            logger.error(f"Error embedding experience batch at index {i}: {e}")

    return updated_count


def main() -> None:
    parser = argparse.ArgumentParser(description="AURA Vector Embedding Backfill Utility")
    parser.add_argument("--batch-size", type=int, default=32, help="Embedding batch size")
    parser.add_argument("--dry-run", action="store_true", help="Scan only, do not write embeddings")
    parser.add_argument("--user-id", type=str, default=None, help="Optional user_id filter")
    parser.add_argument(
        "--target",
        choices=["all", "documents", "memories", "experiences"],
        default="all",
        help="Target entities to backfill",
    )
    args = parser.parse_args()

    t0 = time.time()
    logger.info(f"Starting vector backfill (target={args.target}, dry_run={args.dry_run})...")

    repo_container = create_repository_container(config=settings)
    embedding_provider = create_embedding_provider(config=settings)

    total_backfilled = 0
    if args.target in ("all", "documents"):
        total_backfilled += backfill_knowledge_chunks(repo_container, embedding_provider, args.batch_size, args.dry_run)
    if args.target in ("all", "memories"):
        total_backfilled += backfill_memories(repo_container, embedding_provider, args.user_id, args.batch_size, args.dry_run)
    if args.target in ("all", "experiences"):
        total_backfilled += backfill_experiences(repo_container, embedding_provider, args.user_id, args.batch_size, args.dry_run)

    duration = time.time() - t0
    logger.info(f"Backfill completed in {duration:.2f}s. Total records processed: {total_backfilled}")


if __name__ == "__main__":
    main()
