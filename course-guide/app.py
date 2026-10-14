import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Literal

import httpx
import pymupdf
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from extract import ROOT
from local_llm import CHAT_MODEL, OLLAMA_URL
from retrieval import Retriever
from storage import active_index

retriever: Retriever | None = None
queue = asyncio.Semaphore(1)


@asynccontextmanager
async def lifespan(application: FastAPI):
    global retriever
    retriever = Retriever(active_index())
    yield
    retriever.vectors.close()


app = FastAPI(title="2028 전공연계과목", lifespan=lifespan)


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=1200)
    mode: Literal["vector", "graph", "sql"] = "vector"
    university: str = ""
    model: str = CHAT_MODEL
    top_k: int = Field(default=6, ge=3, le=12)


async def available_models() -> list[str]:
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        response = await client.get(f"{OLLAMA_URL}/api/tags")
        response.raise_for_status()
        return [item["name"] for item in response.json()["models"]
            if "completion" in item.get("capabilities", []) or (not item.get("capabilities") and not any(term in item["name"] for term in ["embed", "bge-m3"]))]


@app.get("/api/status")
async def status():
    try:
        models = await available_models()
        online = True
    except httpx.HTTPError:
        models, online = [], False
    return {**retriever.metadata, "ollama_online": online, "models": models, "default_model": CHAT_MODEL}


@app.post("/api/ask")
async def ask(request: Question):
    question = request.question.strip()
    if len(question) < 2:
        raise HTTPException(422, "질문을 두 글자 이상 입력해 주세요")
    if request.university and request.university not in retriever.metadata["universities"]:
        raise HTTPException(422, "지원하지 않는 대학입니다")
    if queue.locked():
        raise HTTPException(429, "다른 답변을 생성 중입니다. 완료 후 다시 시도해 주세요")
    try:
        if request.model not in await available_models():
            raise HTTPException(422, "Ollama에 설치된 대화 모델을 선택해 주세요")
        async with queue:
            start = time.monotonic()
            result = await retriever.ask(question, request.mode, request.university, request.model, request.top_k)
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result
    except httpx.TimeoutException as error:
        raise HTTPException(504, "로컬 모델 응답 시간이 초과되었습니다. 질문 범위를 좁히거나 작은 모델을 선택해 주세요") from error
    except httpx.HTTPError as error:
        raise HTTPException(503, "Ollama 연결 또는 모델 실행에 실패했습니다. 서버와 모델 설치 상태를 확인해 주세요") from error
    except (ValueError, KeyError, json.JSONDecodeError) as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        import logging
        logging.exception("Question failed")
        raise HTTPException(500, "검색 실행에 실패했습니다. 서버 로그를 확인하고 질문을 구체화해 주세요") from error


@app.get("/api/catalog")
def catalog(university: str = "", field: str = "", category: str = "", search: str = "", page: int = Query(1, ge=1)):
    rows = [row for row in retriever.rows if (not university or row["university"] == university)
            and (not field or row["field"] == field) and (not category or row["category"] == category)
            and (not search or search.replace(" ", "") in (row["department"] + row["course"] + row["condition"]).replace(" ", ""))]
    rows.sort(key=lambda row: (row["university"], row["field"], row["department"], row["category"], row["course"]))
    return {"rows": rows[(page - 1) * 40:page * 40], "total": len(rows), "page": page, "page_size": 40,
            "fields": sorted({row["field"] for row in retriever.rows if not university or row["university"] == university}),
            "categories": sorted({row["category"] for row in retriever.rows})}


@app.get("/api/catalog.csv")
def export_catalog():
    return FileResponse(retriever.directory / "recommendations.csv", filename="admissions-2028.csv", media_type="text/csv; charset=utf-8")


@app.get("/api/documents")
def documents():
    return [{key: value for key, value in document.items() if key != "path"} for document in retriever.documents.values()]


def document_path(document_id: str):
    if document_id not in retriever.documents:
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    return retriever.documents[document_id]["path"]


@app.get("/api/documents/{document_id}/pdf")
def pdf(document_id: str):
    return FileResponse(document_path(document_id), media_type="application/pdf")


@app.get("/api/documents/{document_id}/pages/{page}.png")
def page_image(document_id: str, page: int):
    with pymupdf.open(document_path(document_id)) as document:
        if not 1 <= page <= len(document):
            raise HTTPException(404, "페이지를 찾을 수 없습니다")
        return Response(document[page - 1].get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3)).tobytes("png"), media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static", check_dir=False), name="static")
app.mount("/vendor", StaticFiles(directory=ROOT / "node_modules", check_dir=False), name="vendor")