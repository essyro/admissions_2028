import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / "Admissions-and-Recommended-Courses"
DATA = ROOT / "data"


def normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def needs_ocr(text: str) -> bool:
    letters = re.findall(r"[^\W\d_]", text)
    hangul = re.findall(r"[가-힣]", text)
    return len(text.strip()) < 30 or (len(letters) > 30 and len(hangul) / len(letters) < 0.25)


def ocr_page(page: pymupdf.Page) -> list[dict]:
    binary = ROOT / ".venv" / "bin" / "course-ocr"
    if not binary.exists():
        subprocess.run(["swiftc", str(ROOT / "ocr.swift"), "-o", str(binary)], check=True)
    with tempfile.TemporaryDirectory() as directory:
        image = Path(directory) / "page.png"
        page.get_pixmap(matrix=pymupdf.Matrix(3, 3)).save(image)
        output = subprocess.run([str(binary), str(image)], check=True, capture_output=True, text=True)
    return json.loads(output.stdout)


def extract_document(path: Path) -> dict:
    name = normalize(path.name)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    document = {"id": digest[:16], "sha256": digest, "filename": name,
                "path": str(path), "university": name.split(")")[0].lstrip("("), "pages": []}
    with pymupdf.open(path) as pdf:
        for number, page in enumerate(pdf, 1):
            text = normalize(page.get_text(sort=True))
            method = "pdf-text"
            lines = []
            if needs_ocr(text):
                lines = ocr_page(page)
                text = "\n".join(item["text"] for item in sorted(lines, key=lambda item: (round(item["y0"], 2), item["x0"])))
                method = "apple-vision-ocr"
            tables = []
            if len(pdf) <= 4 or (document["university"] == "서울대" and number in (37, 38)):
                for table in page.find_tables().tables:
                    tables.append({"bbox": list(table.bbox), "rows": table.extract()})
            document["pages"].append({"page": number, "text": text, "method": method,
                                       "ocr_lines": lines, "tables": tables})
            print(f"{name}: {number}/{len(pdf)} {method}", flush=True)
    return document


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file-match", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    target = DATA / "raw"
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(SOURCE.glob("*.pdf")):
        if args.file_match not in normalize(path.name):
            continue
        output = target / (hashlib.sha256(path.read_bytes()).hexdigest()[:16] + ".json")
        if output.exists() and not args.force:
            continue
        result = extract_document(path)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()