# Milvus backend — when, why, how

DebugMind ships two backends out of the box:

| Backend | Default? | Best for |
|---|---|---|
| **SQLite** (`DEBUG_MIND_BACKEND=sqlite`) | yes | < 100k cases. Zero deps, single file. |
| **Chroma** (`DEBUG_MIND_BACKEND=chroma`) | optional | 100k–1M cases. HNSW indexing, still single-process. |
| **Milvus** (planned) | — | > 1M cases, multi-replica, dedicated infra. |

A third backend file exists as a stub:
`src/debug_mind/memory/backends/milvus_backend.py`. This document explains
when the stub becomes worth completing, and what completing it looks like.

---

## When to switch to Milvus

Don't switch just because it's "the industrial choice." Switch when at
least one of the following is true:

1. **Volume**: > 1M cases or you expect to cross that in the next two
   quarters. SQLite and Chroma both work fine up to single-digit hundreds
   of thousands; past that, recall latency on a single Python process
   gets uncomfortable.
2. **Multi-replica**: you need read replicas spread across regions or
   pods, with strong consistency guarantees. SQLite/Chroma are
   single-process stores.
3. **Operational baseline**: your platform team already runs Milvus (or
   another vector database) for other workloads. The deployment cost is
   already paid; reusing it is cheaper than running a second flavour.
4. **GPU acceleration**: you need GPU-backed ANN search at scale.

If none of these apply, the default SQLite backend is the right answer
for a long time. Premature adoption of Milvus adds an etcd + pulsar +
minio dependency to your stack for negligible benefit.

---

## Completing the stub

The `StorageBackend` interface DebugMind asks you to implement
(`src/debug_mind/memory/backends/base.py`) is short:

```python
class StorageBackend(ABC):
    def initialize(self) -> None: ...
    def count(self) -> int: ...
    def search(self, query_embedding, top_k) -> list[dict]: ...
    def upsert(self, ids, embeddings, metadatas) -> None: ...
    def delete(self, ids) -> None: ...
    def get_all_ids(self) -> list[str]: ...
    def rebuild(self) -> None: ...
    def close(self) -> None: ...
```

A production implementation needs to:

1. **Install pymilvus**: `pip install pymilvus`
2. **`initialize`**: `pymilvus.connections.connect(...)`; create the
   collection with fields `id (VARCHAR), embedding (FLOAT_VECTOR, dim=...),
   metadata (JSON)`; create an HNSW index on `embedding`.
3. **`search`**: `collection.search(...)` and convert the `Hit` objects
   into `[{"id": ..., "score": ..., "metadata": ...}]`. Beware: Milvus
   returns *distance*; DebugMind expects similarity in `[0, 1]`. Convert
   with `1 - distance` for L2 or `(1 + cos) / 2` for cosine.
4. **`upsert`**: Milvus does not have a true upsert primitive before
   2.3. For < 2.3 implement as `delete(ids) + insert(...)`.
5. **`get_all_ids`**: `collection.query(expr="id != ''", output_fields=["id"])`.
6. **`rebuild`**: `utility.drop_collection(name)` then re-`initialize`.
7. **`close`**: `pymilvus.connections.disconnect(alias)`.

After completing the stub, wire it into the backend selector in
`MemoryStore._create_backend` (`src/debug_mind/memory/store.py`) by
adding one branch:

```python
if backend_choice == "milvus":
    from debug_mind.memory.backends.milvus_backend import MilvusBackend
    return MilvusBackend(self.memory_dir)
```

and add `pymilvus>=2.3` to the `[project.optional-dependencies]` group
in `pyproject.toml` (e.g. as a new `milvus` extra). Do **not** add it to
the base `dependencies` — Milvus pulls in protobuf and grpc, which is
heavy weight a tiny CLI install should not pay.

---

## Configuration env vars (suggested)

When you implement the backend, settle on the following env vars to
match the rest of the codebase's `DEBUG_MIND_*` convention:

| Var | Default | Meaning |
|---|---|---|
| `DEBUG_MIND_BACKEND` | `sqlite` | Set to `milvus` to select this backend. |
| `DEBUG_MIND_MILVUS_HOST` | `localhost` | Milvus host. |
| `DEBUG_MIND_MILVUS_PORT` | `19530` | Milvus gRPC port. |
| `DEBUG_MIND_MILVUS_COLLECTION` | `debug_mind_cases` | Collection name (namespace-aware!) |

When you add namespace support (Phase 6 already namespaces SQLite/Chroma),
the collection name should embed the namespace, e.g.
`debug_mind_cases__team_a`, otherwise namespaces will share one
collection and the isolation will leak.

---

## Why the stub exists today

Pragmatically: so that during a code or design review the question
"could you swap in Milvus?" has a one-paragraph answer ("yes — the
stub is here, the interface is here, here's what completing it would
take") instead of a hand-wave. It's documentation through code, not a
half-finished feature.
