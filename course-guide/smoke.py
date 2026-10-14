import argparse
import json
import time

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8768")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    cases = [(mode, "경희대", "경희대 컴퓨터공학과의 일반선택과 진로선택 권장과목을 알려줘.") for mode in ["vector", "graph", "sql"]]
    if args.all:
        cases += [("graph", "서울대", "서울대 의과대학의 과목 이수 조건을 알려줘."),
                  ("vector", "부산대", "부산대 경제학부에서 권장하는 과목과 이수 조건을 알려줘."),
                  ("sql", "중앙대", "중앙대에서 일반선택으로 물리학을 권장하는 모집단위 수를 알려줘.")]
    with httpx.Client(base_url=args.url, timeout=600, trust_env=False) as client:
        info = client.get("/api/status").json()
        assert info["ollama_online"]
        for mode, university, question in cases:
            start = time.monotonic()
            response = client.post("/api/ask", json={"question": question, "mode": mode, "university": university,
                                                   "model": info["default_model"], "top_k": 6})
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["mode"] == mode and result["answer"] and result["evidence"]
            assert all(item["university"] == university for item in result["evidence"])
            if university == "경희대":
                if mode == "vector":
                    assert any("컴퓨터공학과" in item["text"] for item in result["evidence"])
                else:
                    general = {row["course"] for row in result["records"] if row["category"] == "일반선택"}
                    career = {row["course"] for row in result["records"] if row["category"] == "진로선택"}
                    assert general == {"대수", "미적분Ⅰ", "확률과 통계"}
                    assert career == {"기하", "미적분Ⅱ", "인공지능 수학"}
            if university == "서울대":
                assert {row["department"] for row in result["records"]} == {"의과대학"}
                assert "세포와 물질대사" in result["answer"] and "생물의 유전" in result["answer"]
            if university == "부산대":
                assert any("모집단위: 경제학부" in item["text"] and "미적분Ⅱ" in item["text"] for item in result["evidence"])
            print(json.dumps({"mode": mode, "university": university, "seconds": round(time.monotonic() - start, 1),
                              "evidence": len(result["evidence"]), "rows": len(result["records"]),
                              "answer": result["answer"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()