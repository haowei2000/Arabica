#!/usr/bin/env python3
"""
Benchmark script for Context Search Agent Harness.
Evaluates retrieval accuracy (Recall@K) and latency for different search methods:
- Grep (Keyword)
- Vector (Semantic)
- Hybrid (Grep + Vector)
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
import random
import sys
import time
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import delete, update

from structure.core.enums import ContextType
from structure.extensions.database import get_session
from structure.models.context.context import Context
from structure.models.workspaces.workspace import Workspace
from structure.schemas.context.context_schema import ContextCreate
from structure.services.context.context_crud import ContextCRUD

EMBEDDING_DIM = 1536


def make_mock_embedding() -> list[float]:
    return [random.uniform(-1, 1) for _ in range(EMBEDDING_DIM)]


@dataclass
class BenchmarkResult:
    method: str
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    avg_latency_ms: float
    total_queries: int


async def run_benchmark(
    db_session,
    user_id: str,
    workspace_id: str,
    dataset: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    mock_embeddings: bool = True,
):
    crud = ContextCRUD(db_session)

    print(f"--- Starting Benchmark for Workspace {workspace_id} ---")
    print(f"Dataset Size: {len(dataset)} documents")
    print(f"Queries: {len(queries)}")
    print(f"Mock Embeddings: {mock_embeddings}")
    print("-" * 50)

    # 1. Preparation: Insert dataset
    print("Inserting dataset...")
    context_objs = []
    for doc in dataset:
        embedding = make_mock_embedding() if mock_embeddings else doc.get("embedding")

        ctx_data = ContextCreate(
            context_type=ContextType.WORKSPACE,
            source_id=workspace_id,
            glance=doc["glance"],
            content=doc["content"],
            tags=doc.get("tags", []),
            meta=doc.get("meta", {}),
        )
        ctx = await crud.create(ctx_data, user_id=user_id)

        if embedding is not None:
            stmt = (
                update(Context)
                .where(Context.id == ctx.id)
                .values(embedding_1536=embedding)
            )
            await db_session.execute(stmt)

        context_objs.append(ctx)

    await db_session.commit()
    print(f"✓ Inserted {len(context_objs)} documents\n")

    methods = ["Grep", "Vector", "Hybrid"]
    results = []

    for method in methods:
        print(f"Testing method: {method}...")
        hits_at_1 = 0
        hits_at_3 = 0
        hits_at_5 = 0
        total_time = 0

        for q in queries:
            query_text = q["query"]
            gold_id = q["gold_id"]

            start_time = time.perf_counter()

            if method == "Grep":
                search_results, _ = await crud.grep(
                    query_text, user_id=user_id, limit=5
                )
            elif method == "Vector":
                q_embedding = (
                    make_mock_embedding() if mock_embeddings else q.get("embedding")
                )
                vector_results = await crud.cosine_search(
                    q_embedding, user_id=user_id, top_k=5
                )
                search_results = [r[0] for r in vector_results]
            elif method == "Hybrid":
                q_embedding = (
                    make_mock_embedding() if mock_embeddings else q.get("embedding")
                )
                hybrid_results = await crud.hybrid_search(
                    query_text, q_embedding, user_id=user_id, top_k=5
                )
                search_results = [r[0] for r in hybrid_results]

            end_time = time.perf_counter()
            total_time += end_time - start_time

            found_ranks = []
            for i, res in enumerate(search_results):
                if gold_id in res.glance or gold_id in res.content:
                    found_ranks.append(i + 1)

            if found_ranks:
                min_rank = min(found_ranks)
                if min_rank <= 1:
                    hits_at_1 += 1
                if min_rank <= 3:
                    hits_at_3 += 1
                if min_rank <= 5:
                    hits_at_5 += 1

        num_queries = len(queries)
        results.append(
            BenchmarkResult(
                method=method,
                recall_at_1=hits_at_1 / num_queries,
                recall_at_3=hits_at_3 / num_queries,
                recall_at_5=hits_at_5 / num_queries,
                avg_latency_ms=(total_time / num_queries) * 1000,
                total_queries=num_queries,
            )
        )

    print("\n" + "=" * 85)
    print(
        f"{'Method':<12} | {'Recall@1':<10} | {'Recall@3':<10} | {'Recall@5':<10} | {'Avg Latency':<15}"
    )
    print("-" * 85)
    for r in results:
        print(
            f"{r.method:<12} | {r.recall_at_1:<10.2%} | {r.recall_at_3:<10.2%} | {r.recall_at_5:<10.2%} | {r.avg_latency_ms:<10.2f} ms"
        )
    print("=" * 85 + "\n")

    return results


async def main():
    random.seed(42)
    user_id = str(uuid4())
    workspace_id = str(uuid4())

    dataset = []
    gold_docs = [
        {
            "glance": "Project Structure Architecture",
            "content": "The system uses an event-sourced architecture with FastAPI and Redis.",
        },
        {
            "glance": "Authentication Guide",
            "content": "Users can authenticate using JWT tokens via the /auth/login endpoint.",
        },
        {
            "glance": "Database Schema: Context",
            "content": "The context table stores vector embeddings and metadata for RAG.",
        },
        {
            "glance": "Worker Node Configuration",
            "content": "Workers scale horizontally using Redis Streams for task distribution.",
        },
        {
            "glance": "API Rate Limiting",
            "content": "Global rate limits are applied per user ID using a sliding window algorithm.",
        },
        {
            "glance": "Frontend: Zustand Store",
            "content": "Global state is managed using Zustand with persistent middleware.",
        },
        {
            "glance": "Docker Deployment Guide",
            "content": "Use docker-compose to spin up the API, worker, and database services.",
        },
        {
            "glance": "MCP Protocol Integration",
            "content": "The Model Context Protocol allows the agent to call external tools.",
        },
        {
            "glance": "Python 3.14 Features",
            "content": "Leveraging new syntax for better type hinting and performance in Python 3.14.",
        },
        {
            "glance": "Vector Search Optimization",
            "content": "Index pgvector columns with HNSW for sub-millisecond similarity search.",
        },
    ]

    dataset.extend(gold_docs)
    for i in range(40):
        dataset.append(
            {
                "glance": f"Random Document {i}",
                "content": f"This is some random noise content for document number {i} to act as a distractor.",
            }
        )

    queries = [
        {"query": "architecture", "gold_id": "Architecture"},
        {"query": "how to login", "gold_id": "Authentication"},
        {"query": "vector search database", "gold_id": "Context"},
        {"query": "scaling workers", "gold_id": "Worker Node"},
        {"query": "rate limits", "gold_id": "Rate Limiting"},
        {"query": "state management", "gold_id": "Zustand"},
        {"query": "docker compose", "gold_id": "Docker Deployment"},
        {"query": "external tools mcp", "gold_id": "MCP Protocol"},
        {"query": "python 3.14", "gold_id": "Python 3.14"},
        {"query": "hnsw index", "gold_id": "Vector Search"},
    ]

    async with get_session("structure") as session:
        ws = Workspace(id=workspace_id, name="Benchmark Workspace", owner_id=user_id)
        session.add(ws)
        await session.commit()

        try:
            await run_benchmark(
                session, user_id, workspace_id, dataset, queries, mock_embeddings=True
            )
        finally:
            await session.execute(
                delete(Context).where(Context.source_id == workspace_id)
            )
            await session.delete(ws)
            await session.commit()
            print("Cleanup completed.")


if __name__ == "__main__":
    asyncio.run(main())
