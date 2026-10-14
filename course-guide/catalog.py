import hashlib
import json
import re
from pathlib import Path

from extract import DATA, SOURCE, normalize

GENERAL = ["대수", "미적분Ⅰ", "확률과 통계", "물리학", "화학", "생명과학", "지구과학"]
CAREER = ["미적분Ⅱ", "기하", "인공지능 수학", "역학과 에너지", "전자기와 양자",
          "물질과 에너지", "화학 반응의 세계", "세포와 물질대사", "생물의 유전",
          "지구시스템과학", "행성우주과학"]
SCIENCE = {"물리학": CAREER[3:5], "화학": CAREER[5:7], "생명과학": CAREER[7:9], "지구과학": CAREER[9:11]}


def compact(text: str) -> str:
    return re.sub(r"\s+", "", normalize(text)).replace("Ⅱ", "II").replace("Ⅰ", "I")


def courses(text: str) -> list[str]:
    remaining = compact(text)
    result = []
    for course in sorted(GENERAL + CAREER, key=lambda value: -len(compact(value))):
        token = compact(course)
        if token in remaining:
            result.append(course)
            remaining = remaining.replace(token, "")
    return result


def departments(text: str) -> list[str]:
    text = text.replace("<경희대>", "").strip()
    text = re.sub(r"(과|학부|전공|과정|\))\n(?=[가-힣A-Z])", r"\1,", text)
    text = text.replace("\n", "").replace(" ", "")
    result, current, depth = [], "", 0
    for character in text:
        depth += (character == "(") - (character == ")")
        if character == "," and depth == 0:
            if current:
                result.append(current)
            current = ""
        else:
            current += character
    if current:
        result.append(current)
    return result


class Catalog:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, doc: dict, page: int, field: str, units: list[str], text: str,
            level: str = "권장", category: str | None = None, note: str = "",
            selected: list[str] | None = None, reviewed: bool = False) -> None:
        found = selected if selected is not None else courses(text)
        if not found:
            found = ["개별 과목 미지정"]
        for unit in units:
            for course in found:
                kind = category or ("일반선택" if course in GENERAL else "진로선택" if course in CAREER else "미지정")
                row = {"university": doc["university"], "field": field, "department": unit,
                       "category": kind, "course": course, "level": level,
                       "condition": text.replace("\n", " "), "note": note,
                       "curriculum": 2022, "admission_year": 2028,
                       "document_id": doc["id"], "page": page,
                       "extraction": doc["pages"][page - 1]["method"],
                       "review_status": "원본 대조" if reviewed else "표 자동 추출"}
                row["id"] = hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
                self.rows.append(row)

    def yonsei(self, doc: dict) -> None:
        for page in doc["pages"][1:]:
            field, college = "", ""
            for row in page["tables"][0]["rows"][2:]:
                field = row[0].replace("\n", "") if row[0] else field
                college = row[1] or college
                units = departments(row[2]) if row[2] and row[2] != "전 모집단위" else [college + " 전 모집단위"]
                if page["page"] == 2:
                    self.add(doc, 2, field, units, "전공연계과목 미제시. 자신의 진로와 적성에 따라 선택 및 이수 권장", level="미지정")
                    continue
                if not row[2]:
                    units = [college]
                self.add(doc, 3, field, units, row[3], category="진로선택")
                general = row[4]
                self.add(doc, 3, field, units, general, category="일반선택",
                         selected=list(SCIENCE) if general == "자율선택" else None,
                         note="자율선택은 4개 과학 일반선택 중 자율 선택" if general == "자율선택" else "")
                self.add(doc, 3, field, units, "과학 진로선택 3과목 이상 이수. 일반선택과목 간 위계 고려",
                         category="진로선택", selected=["과학 진로선택 (개별 과목 미지정)"])

    def kyunghee(self, doc: dict) -> None:
        for page in doc["pages"][2:]:
            field = ""
            for row in page["tables"][0]["rows"][3:]:
                field = row[0].replace("\n", "") if row[0] else field
                units = departments(row[1])
                for position in range(6, 10):
                    if row[position] and row[position] != "-":
                        self.add(doc, page["page"], field, units, row[position],
                                 level="핵심" if position < 8 else "권장",
                                 note="2025.8월 수정 열 기준. 핵심도 지원자격 필수가 아닌 필수적 이수 권장")
        self.add(doc, 1, "인문·자유전공", ["인문계열", "자율/자유전공학부"], "권장과목 별도 미지정", level="미지정")

    def chungang(self, doc: dict) -> None:
        previous = [None] * 4
        for row in doc["pages"][2]["tables"][0]["rows"][3:]:
            row = [value if value is not None else previous[index] for index, value in enumerate(row)]
            previous = row
            units = departments(row[0])
            for position, category in [(1, "진로선택"), (2, "일반선택"), (3, "진로선택")]:
                if row[position] != "-":
                    self.add(doc, 3, "자연계열", units, row[position], category=category)
        self.add(doc, 2, "인문계열", ["인문계열 전 모집단위"], "이수 권장과목 별도 미제시", level="미지정")

    def konkuk(self, doc: dict) -> None:
        page = doc["pages"][1]
        if "2022" not in page["text"]:
            raise ValueError("건국대 2022 교육과정 표 확인 실패")
        configurations = [
            ("인문", ["문과대학", "사범대학(수학교육 제외)"], "국어, 영어, 사회 교과 추천. 개별 과목 미지정", []),
            ("사회과학", ["사회과학대학", "경영대학", "부동산과학원"], "국어, 사회 교과 및 대수, 미적분Ⅰ, 확률과 통계 추천", ["대수", "미적분Ⅰ", "확률과 통계"]),
            ("이학·공학·건축·생명", ["이과대학", "공과대학", "건축대학", "융합생명과학대학", "수의과대학", "사범대학(수학교육)"],
             "대수, 미적분Ⅰ, 미적분Ⅱ, 확률과 통계. 과학은 일반선택과목 먼저 이수", ["대수", "미적분Ⅰ", "미적분Ⅱ", "확률과 통계"]),
            ("자유전공", ["KU자유전공학부"], "계열 상관없이 자신의 진로와 적성에 따라 과목 선택하여 이수", []),
        ]
        for field, units, text, selected in configurations:
            self.add(doc, 2, field, units, text, selected=selected, reviewed=True)
        self.add(doc, 2, "공학", ["공과대학"], "공과대학: 미적분Ⅰ·Ⅱ, 기하 / 물리학 이수 추천", reviewed=True)

    def pusan(self, doc: dict) -> None:
        page = doc["pages"][0]
        bounds = [0.266, 0.313, 0.429, 0.640, 0.804, 0.850, 0.898]
        rules = [
            ("사회과학", "확률과 통계, 미적분Ⅰ, 미적분Ⅱ 중 1과목 이상 이수", []),
            ("수학·정보", "확률과 통계, 기하, 미적분Ⅱ 중 2과목 이상 이수", []),
            ("생명·의약", "화학, 생명과학 관련 일반선택 및 진로선택 중 3과목 이상 이수", ["화학", "생명과학"]),
            ("물리·공학", "기하, 미적분Ⅱ 중 1과목 이상 이수하고, 물리학, 화학 관련 일반선택 및 진로선택 중 3과목 이상 이수", ["물리학", "화학"]),
            ("지구과학", "물리학, 지구과학 관련 일반선택 및 진로선택 중 1과목 이상 이수", ["물리학", "지구과학"]),
            ("융합생명·의공", "물리학, 화학, 생명과학 관련 일반선택 및 진로선택 중 3과목 이상 이수", ["물리학", "화학", "생명과학"]),
        ]
        for index, (field, rule, subjects) in enumerate(rules):
            lines = [line for line in page["ocr_lines"] if .18 < line["x0"] < .65 and bounds[index] < (line["y0"] + line["y1"]) / 2 < bounds[index + 1]]
            lines.sort(key=lambda line: (line["y0"], line["x0"]))
            text = "\n".join(line["text"] for line in lines)
            text = text.replace("AI컴퓨터공학학부", "AI컴퓨터공학부").replace("학•석사", "학·석사").replace("조선·해양", "조선·해양")
            units = departments(text)
            if not units:
                raise ValueError(f"부산대 {index + 1}번 행 OCR 누락")
            selected = courses(rule)
            for subject in subjects:
                selected.extend(SCIENCE[subject])
            self.add(doc, 1, field, units, rule, selected=list(dict.fromkeys(selected)), reviewed=True,
                     note="관련 과목 범위는 원문 하단 참고 기준. 후보 과목 전체 이수 의무가 아님. 2026.6.1 2차 수정")
        self.add(doc, 1, "기타", ["표에 기재되지 않은 모집단위"], "과목 미설정. 관심과 적성에 따라 폭넓게 선택 이수", level="미지정", reviewed=True)

    def seoul(self, doc: dict) -> None:
        if "전공 연계 과목 선택" not in doc["pages"][36]["text"]:
            raise ValueError("서울대 과목 안내 페이지 위치 변경")
        type_one = ["인문대학", "사회과학대학", "간호대학", "경영대학", "농경제사회학부",
                    "교육학과", "국어교육과", "영어교육과", "독어교육과", "불어교육과", "사회교육과",
                    "역사교육과", "지리교육과", "윤리교육과", "소비자아동학부", "식품영양학과", "의류학과"]
        self.add(doc, 37, "유형1", type_one, "제2외국어/한문 1과목 이상 (일반·진로·융합 선택을 통틀어 제시)",
                 category="선택유형 통합", selected=["제2외국어/한문"], reviewed=True)
        type_two = ["자연과학대학", "간호대학", "공과대학", "식물생산과학부", "산림과학부", "식품·동물생명공학부",
                    "응용생물화학부", "조경·지역시스템공학부", "바이오시스템·소재학부", "스마트시스템과학과",
                    "수학교육과", "물리교육과", "화학교육과", "생물교육과", "지구과학교육과", "식품영양학과",
                    "의류학과", "수의과대학", "약학대학", "의과대학", "첨단융합학부", "치의학대학원 치의학과"]
        priority = {}
        for row in doc["pages"][37]["tables"][1]["rows"][1:]:
            labels = row[0]
            for college in ["자연과학대학\n", "공과대학\n", "사범대학\n"]:
                labels = labels.replace(college, "")
            for unit in departments(labels):
                priority[unit] = row[1].replace("\n", " ")
        for unit in dict.fromkeys(type_two + list(priority)):
            math = "기하 또는 미적분Ⅱ" if unit in ["간호대학", "치의학대학원 치의학과"] else "기하, 미적분Ⅱ"
            self.add(doc, 38, "유형2", [unit], math, category="진로선택", reviewed=True)
            science = "과학 진로선택 3과목 이상"
            selected = ["과학 진로선택 (개별 과목 미지정)"]
            if unit == "의과대학":
                science += ". 세포와 물질대사, 생물의 유전을 포함"
                selected += ["세포와 물질대사", "생물의 유전"]
            self.add(doc, 38, "유형2", [unit], science, category="진로선택", selected=selected, reviewed=True)
            if unit in priority:
                self.add(doc, 38, "유형2", [unit], priority[unit], category="일반선택", level="우선 이수 권장", reviewed=True)
        self.add(doc, 38, "유형3", ["학부대학", "미술대학", "체육교육과", "음악대학"], "권장과목 별도 미제시", level="미지정", reviewed=True)


def build_catalog() -> tuple[list[dict], list[dict]]:
    documents = []
    for path in sorted(SOURCE.glob("*.pdf")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        cache = DATA / "raw" / (digest[:16] + ".json")
        if not cache.exists():
            raise ValueError(f"추출 캐시가 없습니다. extract.py를 먼저 실행하세요: {normalize(path.name)}")
        documents.append(json.loads(cache.read_text(encoding="utf-8")))
    catalog = Catalog()
    parsers = {"건국대": catalog.konkuk, "경희대": catalog.kyunghee, "부산대": catalog.pusan,
               "서울대": catalog.seoul, "연세대": catalog.yonsei, "중앙대": catalog.chungang}
    for doc in documents:
        if len(doc["pages"]) <= 4 or doc["university"] == "서울대":
            reviewed_sources = {"건국대": "723e1e094cee531d7082ee90c4f04de7e07c20cda777c01640c3154f7c4491c1",
                                "부산대": "c6ba11e264c491c00009c03fa0d918d932e6e44a2046d9d2c15435680e38badb",
                                "서울대": "c2ee45003dae8495784fc4906e3030371693d670aac631ed09910fd75e2afbfc"}
            if doc["university"] in reviewed_sources and doc["sha256"] != reviewed_sources[doc["university"]]:
                raise ValueError(f"{doc['university']} 원문이 변경되었습니다. OCR 좌표/복합 표 규칙을 재검토하세요")
            parsers[doc["university"]](doc)
    unique = {row["id"]: row for row in catalog.rows}
    rows = list(unique.values())
    if len({row["university"] for row in rows}) != 6:
        raise ValueError("6개 대학 추출 여부를 확인하세요")
    return documents, rows


if __name__ == "__main__":
    documents, rows = build_catalog()
    (DATA / "catalog.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    for university in sorted({row["university"] for row in rows}):
        subset = [row for row in rows if row["university"] == university]
        print(university, len(subset), "relationships", len({row["department"] for row in subset}), "units")