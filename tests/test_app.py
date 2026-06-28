from fastapi.testclient import TestClient

from app import app, deepseek_client, health_client, repository, settings


client = TestClient(app)
deepseek_client.config.deepseek_api_key = ""
if repository.driver is not None:
    repository.driver.close()
repository.driver = None


def test_consult_returns_medical_guidance_for_common_symptoms():
    response = client.post("/api/consult", json={"query": "发烧、咳嗽、嗓子痛两天"})

    assert response.status_code == 200
    data = response.json()
    assert data["diseases"]
    assert data["medicines"]
    assert data["departments"]
    assert "确诊" in data["advice"]


def test_consult_rejects_empty_query():
    response = client.post("/api/consult", json={"query": "   "})

    assert response.status_code == 400
    assert response.json()["detail"] == "请输入症状描述。"


def test_consult_handles_unmatched_query():
    response = client.post("/api/consult", json={"query": "想了解社区活动安排"})

    assert response.status_code == 200
    data = response.json()
    assert data["diseases"] == []
    assert data["medicines"] == []
    assert data["departments"] == []
    assert "未在本地知识库中找到明确匹配" in data["advice"]


def test_health_status_is_safe_without_authorization():
    response = client.get("/api/health/status")

    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "google_health"
    assert "sleep" in data["supported_metrics"]
    assert "spo2" in data["unsupported_metrics"]


def test_consult_includes_google_health_metrics_when_available(monkeypatch):
    async def fake_fetch_metrics():
        return (
            {
                "source": "google_health",
                "device": "Fitbit Inspire HR",
                "synced_at": "2026-05-31T08:00:00Z",
                "sleep_minutes": 330,
                "sleep_stages": {"deep_minutes": 60, "light_minutes": 210, "rem_minutes": 45, "awake_minutes": 15},
                "resting_heart_rate": 102,
                "latest_heart_rate": 108,
                "avg_heart_rate": 88,
                "spo2": None,
            },
            ["Fitbit Inspire HR 不支持血氧数据，spo2 已固定为 null。"],
        )

    monkeypatch.setattr(settings, "health_enable", True)
    monkeypatch.setattr(health_client, "fetch_metrics", fake_fetch_metrics)

    response = client.post("/api/consult", json={"query": "发烧、咳嗽、嗓子痛，昨晚没睡好"})

    assert response.status_code == 200
    data = response.json()
    assert data["health_metrics"]["source"] == "google_health"
    assert data["health_metrics"]["spo2"] is None
    assert "血氧" in data["health_advice"]
    assert "心率" in data["advice"]


def test_voice_status_missing_file_is_safe():
    response = client.get("/api/voice/status")

    assert response.status_code == 200
    assert response.json()["state"]
