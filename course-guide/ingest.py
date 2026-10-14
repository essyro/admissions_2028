import asyncio
import json
import os
import uuid
from datetime import datetime, timezone

from qdrant_client import QdrantClient, models

from catalog import build_catalog
from extract import DATA
from local_llm import EMBED_MODEL, embeddings
from storage import COLLECTION, build_graph, document_chunks, save_relational


async def main() -> None:
    documents, rows = build_catalog()
    directory = DATA / ("index-" + uuid.uuid4().hex[:12])
    directory.mkdir(parents=True)
    graph = build_graph(rows, documents)
    save_relational(directory, documents, rows, graph)
    chunks = document_chunks(documents, rows)
    client = QdrantClient(path=str(directory / "vectors"))
    try:
        for offset in range(0, len(chunks), 16):
            batch = chunks[offset:offset + 16]
            vectors = await embeddings(["search_document: " + item["university"] + " " + item["text"] for item in batch])
            if offset == 0:
                client.create_collection(COLLECTION, vectors_config=models.VectorParams(size=len(vectors[0]), distance=models.Distance.COSINE))
            client.upsert(COLLECTION, points=[models.PointStruct(id=item["id"], vector=vector, payload=item) for item, vector in zip(batch, vectors)])
            print(f"Embedded {min(offset + 16, len(chunks))}/{len(chunks)}", flush=True)
        count = client.count(COLLECTION, exact=True).count
        if count != len(chunks):
            raise ValueError("벡터 저장 개수가 청크 개수와 다릅니다")
    finally:
        client.close()
    metadata = {"created_at": datetime.now(timezone.utc).isoformat(), "documents": len(documents),
                "pages": sum(len(doc["pages"]) for doc in documents), "recommendations": len(rows),
                "departments": len({(row["university"], row["department"]) for row in rows}),
                "chunks": len(chunks), "graph_nodes": graph.number_of_nodes(), "graph_edges": graph.number_of_edges(),
                "embedding_model": EMBED_MODEL, "universities": sorted({row["university"] for row in rows})}
    (directory / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary = DATA / "current.tmp.json"
    temporary.write_text(json.dumps({"index": directory.name}))
    os.replace(temporary, DATA / "current.json")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())