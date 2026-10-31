#!/usr/bin/env python3
"""asset_index — index asset sidecars + pipeline code into Qdrant for semantic retrieval.

Indexed documents
  * *.asset.json sidecars (sfx_library.py tag / ffkit.py probe --sidecar): "forest river ambience, dark,
    loopable, 62 s, -28 LUFS, Maglaj" -> find the right bed/whoosh by description.
  * code + docs (*.py, *.ts, *.tsx, *.md, *.json schemas) chunked by ~60 lines -> "where do I convert HLG".

Storage layout matches the official `mcp-server-qdrant` (payload {"document", "metadata"}, FastEmbed named
vector "fast-<model>"), so Claude/Strata can query the same collection through that MCP server.

  asset_index.py index ~/SFX ~/.config/global-agent-skills/video-production --collection dron-maglaj
  asset_index.py search "dark ambient wind bed for night drone shot" --kind ambience
  asset_index.py index ~/SFX --dry-run            # print documents only (no Qdrant/embeddings needed)

pip install "qdrant-client[fastembed]"   ;   QDRANT_URL defaults to http://127.0.0.1:6333 (use --local DIR for embedded mode)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

MODEL = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")  # mcp-server-qdrant default
CODE_EXT = {".py", ".ts", ".tsx", ".md", ".jsx", ".js"}
SKIP_DIRS = {"node_modules", ".git", ".venv", "__pycache__", "out", "dist", "build"}


def vector_name(model: str) -> str:
    return "fast-" + model.split("/")[-1].lower()


def sidecar_doc(side: dict) -> str:
    parts = [f"{side.get('kind')} asset {Path(side['path']).name}", ", ".join(side.get("tags", []))]
    parts.append(f"{side.get('durationSec', 0):.1f} s")
    loud = side.get("loudness") or {}
    if loud.get("integratedLufs") is not None:
        parts.append(f"{loud['integratedLufs']} LUFS")
    if side.get("loopable"):
        parts.append("loopable bed")
    rec = side.get("recording") or {}
    if rec.get("location"):
        parts.append(f"recorded at {rec['location']}")
    v = side.get("video") or {}
    if v:
        parts.append(f"{v.get('width')}x{v.get('height')} {v.get('fps')} fps {v.get('gamma')}")
    return " | ".join(p for p in parts if p)


def iter_documents(roots: list[str], chunk_lines: int = 60):
    for root in roots:
        for p in Path(root).expanduser().rglob("*"):
            if any(part in SKIP_DIRS for part in p.parts) or not p.is_file():
                continue
            if p.name.endswith(".asset.json"):
                try:
                    side = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                yield sidecar_doc(side), {"type": "asset", "kind": side.get("kind"), "path": side.get("path"),
                                          "tags": side.get("tags", []), "durationSec": side.get("durationSec")}
            elif p.suffix in CODE_EXT or p.name.endswith(".schema.json"):
                if p.stat().st_size > 400_000:
                    continue
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                for i in range(0, max(1, len(lines)), chunk_lines):
                    chunk = "\n".join(lines[i:i + chunk_lines]).strip()
                    if chunk:
                        yield f"{p.name} (lines {i + 1}-{i + len(lines[i:i + chunk_lines])})\n{chunk}", \
                            {"type": "code", "path": str(p.resolve()), "line": i + 1, "lang": p.suffix.lstrip(".")}


def point_id(doc: str, meta: dict) -> str:
    key = f"{meta.get('path')}:{meta.get('line', 0)}:{hashlib.sha1(doc.encode()).hexdigest()}"
    return str(uuid.UUID(hashlib.md5(key.encode()).hexdigest()))


def client(local: str | None):
    from qdrant_client import QdrantClient
    if local:
        return QdrantClient(path=local)
    return QdrantClient(url=os.environ.get("QDRANT_URL", "http://127.0.0.1:6333"), api_key=os.environ.get("QDRANT_API_KEY"))


def index(roots: list[str], collection: str, local: str | None, batch: int = 64) -> dict:
    from fastembed import TextEmbedding
    from qdrant_client import models
    qc = client(local)
    emb = TextEmbedding(MODEL)
    vname = vector_name(MODEL)
    docs = list(iter_documents(roots))
    if not docs:
        return {"indexed": 0}
    dim = len(next(iter(emb.embed([docs[0][0]]))))
    if not qc.collection_exists(collection):
        qc.create_collection(collection, vectors_config={vname: models.VectorParams(size=dim, distance=models.Distance.COSINE)})
    n = 0
    for i in range(0, len(docs), batch):
        chunk = docs[i:i + batch]
        vecs = list(emb.embed([d for d, _ in chunk]))
        qc.upsert(collection, points=[models.PointStruct(id=point_id(d, m), vector={vname: v.tolist()},
                                                         payload={"document": d, "metadata": m})
                                      for (d, m), v in zip(chunk, vecs)])
        n += len(chunk)
    return {"indexed": n, "collection": collection, "vector": vname}


def search(query: str, collection: str, local: str | None, limit: int, kind: str | None) -> list[dict]:
    from fastembed import TextEmbedding
    from qdrant_client import models
    qc = client(local)
    vec = next(iter(TextEmbedding(MODEL).embed([query]))).tolist()
    flt = models.Filter(must=[models.FieldCondition(key="metadata.kind", match=models.MatchValue(value=kind))]) if kind else None
    res = qc.query_points(collection, query=vec, using=vector_name(MODEL), limit=limit, query_filter=flt, with_payload=True)
    return [{"score": round(p.score, 4), **p.payload["metadata"], "document": p.payload["document"][:200]} for p in res.points]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("index"); p.add_argument("roots", nargs="+"); p.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("--limit", type=int, default=8)
    s.add_argument("--kind", choices=["sfx", "ambience", "music", "voiceover", "video", "image"])
    for x in (p, s):
        x.add_argument("--collection", default=os.environ.get("COLLECTION_NAME", "dron-maglaj"))
        x.add_argument("--local", help="embedded Qdrant directory instead of a server")
    a = ap.parse_args()
    if a.cmd == "index":
        if a.dry_run:
            for d, m in iter_documents(a.roots):
                print(json.dumps({"document": d[:160], "metadata": m}, ensure_ascii=False))
            return 0
        print(json.dumps(index(a.roots, a.collection, a.local), indent=2))
    else:
        print(json.dumps(search(a.query, a.collection, a.local, a.limit, a.kind), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
