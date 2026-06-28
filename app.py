from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from health_google import GoogleHealthClient, build_health_advice
from sample_knowledge import COMMON_SYMPTOM_ALIASES, SAMPLE_KNOWLEDGE

try:
    from neo4j import GraphDatabase
except Exception:  # pragma: no cover - keeps the demo usable before dependencies are installed.
    GraphDatabase = None


load_dotenv()


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


class Settings(BaseModel):
    deepseek_api_key: str = Field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""))
    deepseek_base_url: str = Field(default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    deepseek_model: str = Field(default_factory=lambda: os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))
    neo4j_uri: str = Field(default_factory=lambda: os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    neo4j_user: str = Field(default_factory=lambda: os.getenv("NEO4J_USER", "neo4j"))
    neo4j_password: str = Field(default_factory=lambda: os.getenv("NEO4J_PASSWORD", "password"))
    neo4j_timeout: float = Field(default_factory=lambda: float(os.getenv("NEO4J_TIMEOUT", "2")))
    use_sample_fallback: bool = Field(
        default_factory=lambda: os.getenv("USE_SAMPLE_FALLBACK", "true").lower() in {"1", "true", "yes", "on"}
    )
    voice_status_path: str = Field(default_factory=lambda: os.getenv("VOICE_STATUS_PATH", "voice_status.json"))
    health_enable: bool = Field(
        default_factory=lambda: os.getenv("HEALTH_ENABLE", "false").lower() in {"1", "true", "yes", "on"}
    )


class ConsultRequest(BaseModel):
    query: str


class ConsultResponse(BaseModel):
    diseases: List[str]
    medicines: List[str]
    departments: List[str]
    advice: str
    health_metrics: Optional[Dict[str, Any]] = None
    health_advice: str = ""
    health_warnings: List[str] = []
    matched_evidence: List[Dict[str, Any]]
    symptoms: List[str] = []
    warnings: List[str] = []


class HealthStatusResponse(BaseModel):
    provider: str
    enabled: bool
    available: bool
    last_sync_at: str = ""
    device: str = ""
    supported_metrics: List[str] = []
    unsupported_metrics: List[str] = []
    last_error: str = ""


class VoiceStatusResponse(BaseModel):
    state: str
    message: str
    input_device: str = ""
    output_device: str = ""
    asr_engine: str = ""
    remote_asr_url: str = ""
    asr_model_path: str = ""
    wake_word: str = ""
    last_asr_engine: str = ""
    last_asr_latency_ms: int = 0
    last_asr_error: str = ""
    last_transcript: str = ""
    last_advice: str = ""
    last_error: str = ""
    updated_at: str = ""


settings = Settings()


class DeepSeekClient:
    def __init__(self, config: Settings):
        self.config = config

    async def extract_symptoms(self, query: str) -> Tuple[List[str], List[str]]:
        fallback = local_extract_symptoms(query)
        if not self.config.deepseek_api_key:
            return fallback, ["DEEPSEEK_API_KEY 未配置，已使用本地关键词规则解析症状。"]

        prompt = (
            "你是社区就医导向系统的症状解析模块。"
            "请从用户描述中提取中文症状关键词，只返回 JSON，格式为 {\"symptoms\": [\"发热\"]}。"
            "不要输出诊断结论。"
        )
        try:
            data = await self._chat_json(prompt, query)
            symptoms = normalize_symptoms(data.get("symptoms", []))
            return symptoms or fallback, []
        except Exception as exc:
            return fallback, [f"DeepSeek 症状解析失败，已使用本地关键词规则：{exc}"]

    async def organize_answer(
        self,
        query: str,
        symptoms: List[str],
        evidence: List[Dict[str, Any]],
        health_metrics: Optional[Dict[str, Any]] = None,
        health_advice: str = "",
    ) -> Tuple[str, List[str]]:
        if not evidence:
            return "未在本地知识库中找到明确匹配。建议补充症状信息，或咨询社区医生/正规医疗机构。", []
        if not self.config.deepseek_api_key:
            advice = build_local_advice(evidence)
            if health_advice:
                advice += health_advice
            return advice, ["DEEPSEEK_API_KEY 未配置，已使用本地模板组织回答。"]

        system_prompt = (
            "你是社区就医导向系统的回答组织模块。只能基于给定的本地知识库检索结果回答，"
            "不要编造疾病、药物或科室。若给定 Google Health 手环指标，只能作为辅助健康参考，"
            "不能把睡眠或心率解释成诊断结论。血氧为空时必须说明设备不支持，不要推测血氧正常或异常。"
            "请给出简短中文建议，并强调结果不是确诊或处方。"
            "只返回 JSON，格式为 {\"advice\": \"...\"}。"
        )
        user_payload = json.dumps(
            {
                "user_query": query,
                "symptoms": symptoms,
                "retrieved_knowledge": evidence,
                "health_metrics": health_metrics,
                "health_reference_advice": health_advice,
            },
            ensure_ascii=False,
        )
        try:
            data = await self._chat_json(system_prompt, user_payload)
            advice = str(data.get("advice", "")).strip()
            return advice or build_local_advice(evidence), []
        except Exception as exc:
            return build_local_advice(evidence), [f"DeepSeek 结果组织失败，已使用本地模板：{exc}"]

    async def _chat_json(self, system_prompt: str, user_content: str) -> Dict[str, Any]:
        url = self.config.deepseek_base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.config.deepseek_api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.config.deepseek_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)


class MedicalKnowledgeRepository:
    def __init__(self, config: Settings):
        self.config = config
        self.driver = None
        if GraphDatabase is not None:
            try:
                self.driver = GraphDatabase.driver(
                    config.neo4j_uri,
                    auth=(config.neo4j_user, config.neo4j_password),
                    connection_timeout=config.neo4j_timeout,
                    max_connection_lifetime=60,
                )
            except Exception:
                self.driver = None

    def close(self) -> None:
        if self.driver is not None:
            self.driver.close()

    def search(self, symptoms: List[str], query: str) -> Tuple[List[Dict[str, Any]], List[str]]:
        warnings: List[str] = []
        if self.driver is not None:
            try:
                return self._search_neo4j(symptoms), warnings
            except Exception as exc:
                warnings.append(f"Neo4j 查询失败，已切换到兜底数据：{type(exc).__name__}")

        if self.config.use_sample_fallback:
            warnings.append("已使用内置示例知识库兜底；部署时请启动 Neo4j 并运行 import_data.py。")
            return search_sample_knowledge(symptoms, query), warnings

        warnings.append("Neo4j 不可用，且 USE_SAMPLE_FALLBACK=false。")
        return [], warnings

    def _search_neo4j(self, symptoms: List[str]) -> List[Dict[str, Any]]:
        if not symptoms:
            return []
        with self.driver.session() as session:
            rows = session.run(
                """
                MATCH (d:Disease)-[:HAS_SYMPTOM]->(s:Symptom)
                WHERE s.name IN $symptoms
                WITH d, collect(DISTINCT s.name) AS matched_symptoms, count(DISTINCT s) AS score
                OPTIONAL MATCH (d)-[:TREATED_BY|RECOMMENDED_DRUG]->(m:Medicine)
                WITH d, matched_symptoms, score, collect(DISTINCT m.name) AS medicines
                OPTIONAL MATCH (d)-[:VISIT_DEPARTMENT]->(dept:Department)
                RETURN d.name AS disease,
                       coalesce(d.description, "") AS description,
                       matched_symptoms,
                       medicines,
                       collect(DISTINCT dept.name) AS departments,
                       score
                ORDER BY score DESC, disease ASC
                LIMIT 5
                """,
                symptoms=symptoms,
            )
            return [dict(row) for row in rows]


deepseek_client = DeepSeekClient(settings)
repository = MedicalKnowledgeRepository(settings)
health_client = GoogleHealthClient()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    repository.close()


app = FastAPI(
    title="树莓派智能就医导向系统",
    version="0.1.0",
    lifespan=lifespan,
    default_response_class=UTF8JSONResponse,
)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return Path("static/index.html").read_text(encoding="utf-8")


@app.post("/api/consult", response_model=ConsultResponse)
async def consult(payload: ConsultRequest) -> ConsultResponse:
    query = payload.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="请输入症状描述。")

    symptoms, extraction_warnings = await deepseek_client.extract_symptoms(query)
    evidence, search_warnings = repository.search(symptoms, query)
    health_metrics, health_warnings = await health_client.fetch_metrics()
    health_advice = build_health_advice(health_metrics, health_warnings) if settings.health_enable else ""
    advice, answer_warnings = await deepseek_client.organize_answer(
        query, symptoms, evidence, health_metrics, health_advice
    )

    return ConsultResponse(
        diseases=unique_list(item["disease"] for item in evidence),
        medicines=unique_list(medicine for item in evidence for medicine in item.get("medicines", [])),
        departments=unique_list(dept for item in evidence for dept in item.get("departments", [])),
        advice=advice,
        health_metrics=health_metrics,
        health_advice=health_advice,
        health_warnings=health_warnings,
        matched_evidence=evidence,
        symptoms=symptoms,
        warnings=extraction_warnings + search_warnings + answer_warnings,
    )


@app.get("/api/health/status", response_model=HealthStatusResponse)
async def health_status() -> HealthStatusResponse:
    return HealthStatusResponse(**await health_client.status())


@app.get("/api/voice/status", response_model=VoiceStatusResponse)
async def voice_status() -> VoiceStatusResponse:
    status_path = Path(settings.voice_status_path)
    if not status_path.is_absolute():
        status_path = Path.cwd() / status_path
    if not status_path.exists():
        return VoiceStatusResponse(
            state="missing",
            message="语音服务尚未写入状态文件。",
            asr_model_path=os.getenv("ASR_MODEL_PATH", ""),
            asr_engine=os.getenv("ASR_ENGINE", "vosk"),
            remote_asr_url=os.getenv("REMOTE_ASR_URL", ""),
            wake_word=os.getenv("WAKE_WORD", "小医小医"),
        )
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return VoiceStatusResponse(state="error", message=f"语音状态文件读取失败：{exc}", last_error=str(exc))
    return VoiceStatusResponse(
        state=str(data.get("state", "unknown")),
        message=str(data.get("message", "")),
        input_device=str(data.get("input_device", "")),
        output_device=str(data.get("output_device", "")),
        asr_engine=str(data.get("asr_engine", "")),
        remote_asr_url=str(data.get("remote_asr_url", "")),
        asr_model_path=str(data.get("asr_model_path", "")),
        wake_word=str(data.get("wake_word", "")),
        last_asr_engine=str(data.get("last_asr_engine", "")),
        last_asr_latency_ms=int(data.get("last_asr_latency_ms", 0) or 0),
        last_asr_error=str(data.get("last_asr_error", "")),
        last_transcript=str(data.get("last_transcript", "")),
        last_advice=str(data.get("last_advice", "")),
        last_error=str(data.get("last_error", "")),
        updated_at=str(data.get("updated_at", "")),
    )


def local_extract_symptoms(query: str) -> List[str]:
    normalized_query = query
    found: List[str] = []
    for alias, standard in COMMON_SYMPTOM_ALIASES.items():
        if alias in normalized_query:
            found.append(standard)
            normalized_query = normalized_query.replace(alias, standard)

    known_symptoms = sorted(
        {symptom for item in SAMPLE_KNOWLEDGE for symptom in item["symptoms"]},
        key=len,
        reverse=True,
    )
    for symptom in known_symptoms:
        if symptom in normalized_query:
            found.append(symptom)

    tokens = re.findall(r"[\u4e00-\u9fff]{2,6}", normalized_query)
    for token in tokens:
        if token in known_symptoms:
            found.append(token)
    return normalize_symptoms(found)


def normalize_symptoms(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    normalized: List[str] = []
    for value in values:
        text = str(value).strip()
        text = COMMON_SYMPTOM_ALIASES.get(text, text)
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def search_sample_knowledge(symptoms: List[str], query: str) -> List[Dict[str, Any]]:
    scored: List[Dict[str, Any]] = []
    for item in SAMPLE_KNOWLEDGE:
        matched = [symptom for symptom in item["symptoms"] if symptom in symptoms or symptom in query]
        if matched:
            scored.append(
                {
                    "disease": item["disease"],
                    "description": item["description"],
                    "matched_symptoms": matched,
                    "medicines": item["medicines"],
                    "departments": item["departments"],
                    "score": len(matched),
                }
            )
    scored.sort(key=lambda item: (-item["score"], item["disease"]))
    return scored[:5]


def build_local_advice(evidence: List[Dict[str, Any]]) -> str:
    primary = evidence[0]
    disease = primary["disease"]
    departments = "、".join(primary.get("departments", [])) or "全科"
    medicines = "、".join(primary.get("medicines", [])[:3]) or "遵医嘱用药"
    return (
        f"根据本地知识库匹配，较相关的疾病方向是 {disease}。"
        f"可优先咨询 {departments}，知识库中关联的常见药物包括 {medicines}。"
        "以上内容仅用于社区就医导向，不构成确诊或处方；症状持续、加重或出现高热胸痛等情况请及时就医。"
    )


def unique_list(values) -> List[str]:
    result: List[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
