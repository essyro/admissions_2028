import json
import os

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gpt-oss:20b")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3:latest")


async def generate(system: str, prompt: str, model: str = CHAT_MODEL, structured: bool = False) -> str:
    body = {"model": model, "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 1800}, "keep_alive": "15m"}
    if structured:
        body["format"] = "json"
    if model.startswith("gpt-oss"):
        body["think"] = "low"
    async with httpx.AsyncClient(timeout=300, trust_env=False) as client:
        response = await client.post(f"{OLLAMA_URL}/api/chat", json=body)
        response.raise_for_status()
        data = response.json()
    if data.get("done_reason") == "length":
        raise ValueError("모델 출력 한도를 초과했습니다. 질문 범위를 좁혀 주세요.")
    return data["message"]["content"]


async def embeddings(texts: list[str], model: str = EMBED_MODEL) -> list[list[float]]:
    async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
        response = await client.post(f"{OLLAMA_URL}/api/embed", json={"model": model, "input": texts, "truncate": False})
        response.raise_for_status()
    result = response.json()["embeddings"]
    if len(result) != len(texts) or any(not vector for vector in result):
        raise ValueError("임베딩 응답 개수가 입력과 다릅니다")
    return result


async def json_generate(system: str, prompt: str, model: str = CHAT_MODEL) -> dict:
    result = json.loads(await generate(system, prompt, model, structured=True))
    if not isinstance(result, dict):
        raise ValueError("모델 JSON 응답 형식이 올바르지 않습니다")
    return result