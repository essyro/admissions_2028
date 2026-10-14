from catalog import build_catalog, courses, departments
from storage import build_graph, document_chunks, save_relational, split_text
import sqlite3
import pytest
from retrieval import execute_readonly_sql, Retriever
from storage import active_index


def test_course_names_and_departments():
    assert set(courses("대수, 미적분I, 미적분II, 화학 반응의 세계")) == {"대수", "미적분Ⅰ", "미적분Ⅱ", "화학 반응의 세계"}
    assert departments("식품공학부(식품공학, 식품영양)\n간호학과") == ["식품공학부(식품공학,식품영양)", "간호학과"]


def test_real_catalog():
    documents, rows = build_catalog()
    assert len(documents) == 11
    assert len({row["university"] for row in rows}) == 6
    assert all(row["curriculum"] == 2022 for row in rows)
    assert all(0 < row["page"] <= len(next(doc for doc in documents if doc["id"] == row["document_id"])["pages"]) for row in rows)
    geography = [row for row in rows if row["university"] == "경희대" and row["department"] == "지리학과"]
    assert {row["course"] for row in geography} == {"대수", "미적분Ⅰ", "확률과 통계", "지구과학"}
    central = [row for row in rows if row["university"] == "중앙대" and row["department"] == "융합공학부"]
    assert any(row["course"] == "물질과 에너지" for row in central)
    pusan = [row for row in rows if row["university"] == "부산대"]
    assert any(row["department"] == "경제학부" and row["course"] == "미적분Ⅱ" for row in pusan)
    assert any(row["department"] == "의예과" and "3과목" in row["condition"] for row in pusan)


def test_storage_and_graph(tmp_path):
    documents, rows = build_catalog()
    graph = build_graph(rows, documents)
    save_relational(tmp_path, documents, rows, graph)
    with sqlite3.connect(tmp_path / "admissions.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM recommendations").fetchone()[0] == len(rows)
        assert connection.execute("SELECT count(*) FROM graph_edges").fetchone()[0] == graph.number_of_edges()
    assert len([node for node, attrs in graph.nodes(data=True) if attrs["kind"] == "recommendation"]) == len(rows)
    assert all(graph.out_degree(node) == 2 for node, attrs in graph.nodes(data=True) if attrs["kind"] == "recommendation")
    chunks = document_chunks(documents)
    assert {chunk["document_id"] for chunk in chunks} == {doc["id"] for doc in documents}
    assert max(len(chunk["text"]) for chunk in chunks) <= 650
    assert split_text("123456789", size=5, overlap=1) == ["12345", "56789", "9"]
    corrected = document_chunks(documents, rows)
    economics = next(chunk for chunk in corrected if chunk["university"] == "부산대" and "모집단위: 경제학부" in chunk["text"])
    assert "진로선택: 미적분Ⅱ" in economics["text"]
    assert "중 1과목 이상" in economics["text"]


def test_readonly_sql():
    _, rows = build_catalog()
    result, truncated = execute_readonly_sql("SELECT university, COUNT(*) AS total FROM recommendations GROUP BY university", rows)
    assert len(result) == 6 and not truncated
    assert sum(row["total"] for row in result) == len(rows)
    for sql in ["DELETE FROM recommendations", "SELECT * FROM recommendations; DROP TABLE recommendations", "SELECT * FROM sqlite_master", "SELECT load_extension('bad') FROM recommendations", "SELECT randomblob(999999999) FROM recommendations"]:
        with pytest.raises((ValueError, sqlite3.DatabaseError)):
            execute_readonly_sql(sql, rows)
    limited, truncated = execute_readonly_sql("SELECT * FROM recommendations", rows, limit=5)
    assert len(limited) == 5 and truncated


def test_graph_traversal():
    documents, rows = build_catalog()
    engine = Retriever.__new__(Retriever)
    engine.graph = build_graph(rows, documents)
    engine.documents = {document["id"]: document for document in documents}
    result = engine.graph_search(["경희대", "컴퓨터"], "경희대")
    assert result["records"]
    assert {row["department"] for row in result["records"]} == {"컴퓨터공학과"}
    assert {row["university"] for row in result["records"]} == {"경희대"}
    assert any(row["course"] == "인공지능 수학" for row in result["records"])
    reverse = engine.graph_search(["전자기와 양자"], "중앙대")
    assert reverse["records"]
    assert all(row["course"] == "전자기와 양자" and row["university"] == "중앙대" for row in reverse["records"])
    exact = engine.graph_search(["화학 반응의 세계"], "중앙대")
    assert {row["course"] for row in exact["records"]} == {"화학 반응의 세계"}
    assert "과목=인공지능 수학" in result["evidence"][0]["text"]
    medicine = engine.graph_search(["의과대학"], "서울대")
    assert {row["department"] for row in medicine["records"]} == {"의과대학"}
    assert {row["course"] for row in medicine["records"]} >= {"기하", "미적분Ⅱ", "생명과학", "세포와 물질대사", "생물의 유전"}