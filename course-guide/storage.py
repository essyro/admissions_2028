import csv
import hashlib
import json
import sqlite3
from pathlib import Path

import networkx as nx

from extract import DATA

COLLECTION = "admissions_2028"
COLUMNS = ["id", "university", "field", "department", "category", "course", "level", "condition", "note",
           "curriculum", "admission_year", "document_id", "page", "extraction", "review_status"]


def active_index() -> Path:
    pointer = DATA / "current.json"
    if not pointer.exists():
        raise FileNotFoundError("먼저 .venv/bin/python ingest.py 를 실행해 주세요")
    return DATA / json.loads(pointer.read_text())["index"]


def node_id(kind: str, label: str) -> str:
    return kind + ":" + hashlib.sha256(label.encode()).hexdigest()[:20]


def build_graph(rows: list[dict], documents: list[dict]) -> nx.DiGraph:
    graph = nx.DiGraph()
    names = {doc["id"]: doc["filename"] for doc in documents}
    for row in rows:
        university = node_id("university", row["university"])
        field = node_id("field", row["university"] + row["field"])
        department = node_id("department", row["university"] + ":" + row["department"])
        recommendation = "recommendation:" + row["id"]
        course = node_id("course", row["course"])
        category = node_id("category", row["category"])
        page = f"page:{row['document_id']}:{row['page']}"
        for identifier, kind, label in [(university, "university", row["university"]),
                                        (field, "field", row["field"]), (department, "department", row["department"]),
                                        (course, "course", row["course"]), (category, "category", row["category"]),
                                        (page, "page", f"{names[row['document_id']]} p.{row['page']}")]:
            graph.add_node(identifier, kind=kind, label=label)
        graph.add_node(recommendation, kind="recommendation", label=row["condition"], record=row)
        for source, target, relation in [(university, field, "HAS_FIELD"), (field, department, "INCLUDES"),
                                          (department, recommendation, "HAS_RECOMMENDATION"),
                                          (recommendation, course, "RECOMMENDS"), (course, category, "CLASSIFIED_AS"),
                                          (recommendation, page, "EVIDENCED_BY")]:
            graph.add_edge(source, target, relation=relation)
    return graph


def save_relational(directory: Path, documents: list[dict], rows: list[dict], graph: nx.DiGraph) -> None:
    with sqlite3.connect(directory / "admissions.sqlite3") as connection:
        connection.executescript("""
            CREATE TABLE documents (id TEXT PRIMARY KEY, university TEXT, filename TEXT, path TEXT, sha256 TEXT, pages INTEGER);
            CREATE TABLE pages (document_id TEXT, page INTEGER, text TEXT, extraction TEXT, PRIMARY KEY(document_id, page));
            CREATE TABLE recommendations (
                id TEXT PRIMARY KEY, university TEXT, field TEXT, department TEXT, category TEXT,
                course TEXT, level TEXT, condition TEXT, note TEXT, curriculum INTEGER,
                admission_year INTEGER, document_id TEXT, page INTEGER, extraction TEXT, review_status TEXT);
            CREATE INDEX idx_recommendations_scope ON recommendations(university, department, category);
            CREATE TABLE graph_nodes (id TEXT PRIMARY KEY, kind TEXT, label TEXT, properties TEXT);
            CREATE TABLE graph_edges (source TEXT, target TEXT, relation TEXT, PRIMARY KEY(source,target));
        """)
        connection.executemany("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
                               [(doc["id"], doc["university"], doc["filename"], doc["path"], doc["sha256"], len(doc["pages"])) for doc in documents])
        connection.executemany("INSERT INTO pages VALUES (?, ?, ?, ?)",
                               [(doc["id"], page["page"], page["text"], page["method"]) for doc in documents for page in doc["pages"]])
        connection.executemany(f"INSERT INTO recommendations VALUES ({','.join('?' for _ in COLUMNS)})",
                               [[row[key] for key in COLUMNS] for row in rows])
        connection.executemany("INSERT INTO graph_nodes VALUES (?, ?, ?, ?)",
                               [(identifier, attrs["kind"], attrs["label"], json.dumps(attrs, ensure_ascii=False)) for identifier, attrs in graph.nodes(data=True)])
        connection.executemany("INSERT INTO graph_edges VALUES (?, ?, ?)",
                               [(source, target, attrs["relation"]) for source, target, attrs in graph.edges(data=True)])
        assert connection.execute("SELECT count(*) FROM recommendations").fetchone()[0] == len(rows)
    (directory / "graph.json").write_text(json.dumps(nx.node_link_data(graph), ensure_ascii=False), encoding="utf-8")
    with (directory / "recommendations.csv").open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def split_text(text: str, size: int = 650, overlap: int = 90) -> list[str]:
    if not 0 <= overlap < size:
        raise ValueError("청크 overlap은 size보다 작아야 합니다")
    text = text.strip()
    return [text[start:start + size] for start in range(0, len(text), size - overlap) if text[start:start + size].strip()]


def document_chunks(documents: list[dict], reviewed_rows: list[dict] | None = None) -> list[dict]:
    chunks = []
    for doc in documents:
        for page in doc["pages"]:
            if doc["university"] == "건국대" and len(doc["pages"]) == 2 and page["page"] == 1:
                continue
            text = page["text"]
            corrected = [row for row in (reviewed_rows or []) if row["document_id"] == doc["id"] and row["page"] == page["page"] and row["review_status"] == "원본 대조"]
            if corrected:
                units = {}
                for row in corrected:
                    units.setdefault(row["department"], []).append(row)
                parts = []
                for unit, records in units.items():
                    categories = {}
                    for row in records:
                        categories.setdefault(row["category"], []).append(row["course"])
                    conditions = list(dict.fromkeys(row["condition"] for row in records))
                    parts.append(f"{doc['university']} 2028학년도 2022 개정 교육과정\n모집단위: {unit}\n" +
                                 "\n".join(f"{category}: {', '.join(dict.fromkeys(values))}" for category, values in categories.items()) +
                                 "\n원문 조건: " + " / ".join(conditions))
                page_chunks = [chunk for part in parts for chunk in split_text(part)]
            elif len(doc["pages"]) <= 4 and page["tables"]:
                parts = []
                for table in page["tables"]:
                    previous = []
                    if doc["university"] == "경희대" and page["page"] >= 3:
                        header = "학문분야 | 모집단위 | 수학 핵심과목 | 과학 핵심과목 | 수학 권장과목 | 과학 권장과목"
                        data_rows = table["rows"][3:]
                    elif doc["university"] == "연세대":
                        header = "계열 | 대학 | 모집단위 | 수학 진로선택 | 과학 일반선택 | 과학 진로선택"
                        data_rows = table["rows"][2:]
                    elif doc["university"] == "중앙대" and page["page"] == 3:
                        header = "모집단위 | 수학 진로선택 | 과학 일반선택 | 과학 진로선택"
                        data_rows = table["rows"][3:]
                    else:
                        header = " | ".join(str(cell or "") for cell in table["rows"][0])
                        data_rows = table["rows"][1:]
                    for cells in data_rows:
                        if doc["university"] == "경희대" and page["page"] >= 3:
                            cells = cells[:2] + cells[6:]
                        filled = [cell if cell is not None else previous[index] if index < len(previous) else "" for index, cell in enumerate(cells)]
                        previous = filled
                        parts.append(doc["university"] + " 2028학년도 2022 개정 교육과정 (수정 후)\n" + header + "\n" + " | ".join((cell or "").replace("\n", "") for cell in filled))
                page_chunks = [chunk for part in parts for chunk in split_text(part)]
            else:
                page_chunks = split_text(text)
            for index, part in enumerate(page_chunks):
                chunks.append({"id": len(chunks), "document_id": doc["id"], "university": doc["university"],
                               "filename": doc["filename"], "page": page["page"], "chunk": index,
                               "text": part, "extraction": page["method"], "is_guide": len(doc["pages"]) <= 4 or (doc["university"] == "서울대" and page["page"] in [37, 38])})
    return chunks