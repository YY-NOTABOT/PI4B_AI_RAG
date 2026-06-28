from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx


GOOGLE_HEALTH_BASE_URL = "https://health.googleapis.com/v4/users/me"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


@dataclass
class GoogleHealthSettings:
    enabled: bool = field(
        default_factory=lambda: os.getenv("HEALTH_ENABLE", "false").lower() in {"1", "true", "yes", "on"}
    )
    provider: str = field(default_factory=lambda: os.getenv("HEALTH_PROVIDER", "google_health"))
    client_id: str = field(default_factory=lambda: os.getenv("GOOGLE_HEALTH_CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: os.getenv("GOOGLE_HEALTH_CLIENT_SECRET", ""))
    token_path: Path = field(default_factory=lambda: Path(os.getenv("GOOGLE_HEALTH_TOKEN_PATH", "data/google_health_token.json")))
    timeout: float = field(default_factory=lambda: float(os.getenv("GOOGLE_HEALTH_TIMEOUT", "5")))
    lookback_days: int = field(default_factory=lambda: int(os.getenv("GOOGLE_HEALTH_LOOKBACK_DAYS", "2")))
    proxy_consult_url: str = field(default_factory=lambda: os.getenv("HEALTH_PROXY_CONSULT_URL", ""))


class GoogleHealthClient:
    def __init__(self, settings: Optional[GoogleHealthSettings] = None):
        self.settings = settings or GoogleHealthSettings()
        self.last_error = ""

    async def status(self) -> Dict[str, Any]:
        if not self.settings.enabled:
            return self._status(available=False, last_error="Google Health 未启用。")
        if self.settings.proxy_consult_url:
            metrics, error = await self._fetch_proxy_metrics()
            if metrics:
                return self._status(available=True, last_error="", last_sync_at=str(metrics.get("synced_at", "")))
            if error:
                return self._status(available=False, last_error=error)
        token, error = self._read_token()
        if error:
            return self._status(available=False, last_error=error)
        if self._token_expired(token):
            _, error = await self._refresh_access_token(token)
            if error:
                return self._status(available=False, last_error=error)
        return self._status(available=True, last_error="")

    async def fetch_metrics(self) -> Tuple[Optional[Dict[str, Any]], List[str]]:
        if not self.settings.enabled:
            return None, []
        if self.settings.proxy_consult_url:
            metrics, error = await self._fetch_proxy_metrics()
            if metrics:
                warnings = list(metrics.get("warnings") or [])
                warnings.append("健康指标由电脑端 Google Health 服务代理返回。")
                metrics["warnings"] = warnings
                return metrics, warnings
            if error:
                return None, [error]

        token, error = self._read_token()
        if error:
            return None, [error]
        if self._token_expired(token):
            token, error = await self._refresh_access_token(token)
            if error:
                return None, [error]

        access_token = str(token.get("access_token", ""))
        warnings: List[str] = ["Fitbit Inspire HR 不支持血氧数据，spo2 已固定为 null。"]
        start_date = date.today() - timedelta(days=max(self.settings.lookback_days, 1))
        start_time = datetime.now(timezone.utc) - timedelta(days=max(self.settings.lookback_days, 1))

        sleep_data, sleep_warning = await self._fetch_sleep(access_token, start_date)
        heart_rate_data, hr_warning = await self._fetch_heart_rate(access_token, start_time)
        resting_hr_data, rhr_warning = await self._fetch_resting_heart_rate(access_token, start_date)
        warnings.extend(w for w in [sleep_warning, hr_warning, rhr_warning] if w)

        metrics = normalize_health_metrics(
            sleep_data=sleep_data,
            heart_rate_data=heart_rate_data,
            resting_hr_data=resting_hr_data,
            warnings=warnings,
        )
        if not metrics:
            return None, warnings or ["Google Health 未返回可用睡眠或心率数据。"]
        return metrics, warnings

    async def _fetch_proxy_metrics(self) -> Tuple[Optional[Dict[str, Any]], str]:
        payload = {"query": "健康指标同步检查"}
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
                response = await client.post(self.settings.proxy_consult_url, json=payload)
                response.raise_for_status()
            data = response.json()
        except Exception as exc:
            return None, f"电脑端健康代理不可用：{exc}"
        metrics = data.get("health_metrics")
        if isinstance(metrics, dict) and metrics:
            return metrics, ""
        warnings = data.get("health_warnings") or []
        return None, "电脑端健康代理未返回可用指标：" + " ".join(str(item) for item in warnings)

    async def _fetch_sleep(self, access_token: str, start_date: date) -> Tuple[Dict[str, Any], str]:
        return await self._get_data(
            access_token,
            "/dataTypes/sleep/dataPoints:reconcile",
            {
                "dataSourceFamily": "users/me/dataSourceFamilies/google-wearables",
                "filter": f'sleep.interval.civil_end_time >= "{start_date.isoformat()}"',
            },
            "睡眠数据读取失败",
        )

    async def _fetch_heart_rate(self, access_token: str, start_time: datetime) -> Tuple[Dict[str, Any], str]:
        return await self._get_data(
            access_token,
            "/dataTypes/heart-rate/dataPoints",
            {"filter": f'heart_rate.sample_time.physical_time >= "{start_time.isoformat().replace("+00:00", "Z")}"'},
            "心率数据读取失败",
        )

    async def _fetch_resting_heart_rate(self, access_token: str, start_date: date) -> Tuple[Dict[str, Any], str]:
        return await self._get_data(
            access_token,
            "/dataTypes/daily-resting-heart-rate/dataPoints:reconcile",
            {"filter": f'daily_resting_heart_rate.date >= "{start_date.isoformat()}"'},
            "静息心率读取失败",
        )

    async def _get_data(
        self, access_token: str, endpoint: str, params: Dict[str, str], error_prefix: str
    ) -> Tuple[Dict[str, Any], str]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
                response = await client.get(GOOGLE_HEALTH_BASE_URL + endpoint, headers=headers, params=params)
                response.raise_for_status()
            return response.json(), ""
        except Exception as exc:
            return {}, f"{error_prefix}：{exc}"

    def _read_token(self) -> Tuple[Dict[str, Any], str]:
        path = self.settings.token_path
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            return {}, f"Google Health token 文件不存在：{path}"
        try:
            return json.loads(path.read_text(encoding="utf-8")), ""
        except Exception as exc:
            return {}, f"Google Health token 文件读取失败：{exc}"

    def _write_token(self, token: Dict[str, Any]) -> None:
        path = self.settings.token_path
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(token, ensure_ascii=False, indent=2), encoding="utf-8")

    def _token_expired(self, token: Dict[str, Any]) -> bool:
        expires_at = float(token.get("expires_at", 0) or 0)
        return not token.get("access_token") or expires_at <= time.time() + 60

    async def _refresh_access_token(self, token: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        refresh_token = str(token.get("refresh_token", ""))
        if not refresh_token:
            return token, "Google Health token 缺少 refresh_token，请重新授权。"
        if not self.settings.client_id or not self.settings.client_secret:
            return token, "GOOGLE_HEALTH_CLIENT_ID 或 GOOGLE_HEALTH_CLIENT_SECRET 未配置。"

        payload = {
            "client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
                response = await client.post(GOOGLE_TOKEN_URL, data=payload)
                response.raise_for_status()
            new_values = response.json()
        except Exception as exc:
            return token, f"Google Health token 刷新失败：{exc}"

        token.update(new_values)
        token["expires_at"] = time.time() + int(new_values.get("expires_in", 3600))
        if "refresh_token" not in new_values:
            token["refresh_token"] = refresh_token
        self._write_token(token)
        return token, ""

    def _status(self, available: bool, last_error: str, last_sync_at: str = "") -> Dict[str, Any]:
        self.last_error = last_error
        return {
            "provider": "google_health",
            "enabled": self.settings.enabled,
            "available": available,
            "last_sync_at": last_sync_at,
            "device": "Fitbit Inspire HR",
            "supported_metrics": ["sleep", "heart_rate", "resting_heart_rate"],
            "unsupported_metrics": ["spo2"],
            "last_error": last_error,
        }


def normalize_health_metrics(
    sleep_data: Dict[str, Any], heart_rate_data: Dict[str, Any], resting_hr_data: Dict[str, Any], warnings: List[str]
) -> Dict[str, Any]:
    sleep_points = _data_points(sleep_data)
    heart_points = _data_points(heart_rate_data)
    resting_points = _data_points(resting_hr_data)

    sleep_summary = _latest_sleep_summary(sleep_points)
    latest_hr = _latest_measurement(heart_points, ["beatsPerMinute", "bpm", "value"])
    resting_hr = _latest_measurement(resting_points, ["beatsPerMinute", "bpm", "value"])
    avg_hr = _average_measurement(heart_points, ["beatsPerMinute", "bpm", "value"])

    metrics: Dict[str, Any] = {
        "source": "google_health",
        "device": "Fitbit Inspire HR",
        "synced_at": _latest_timestamp(sleep_points + heart_points + resting_points),
        "sleep_minutes": sleep_summary.get("sleep_minutes"),
        "sleep_stages": sleep_summary.get("sleep_stages", {}),
        "resting_heart_rate": resting_hr,
        "latest_heart_rate": latest_hr,
        "avg_heart_rate": avg_hr,
        "spo2": None,
        "warnings": warnings,
    }
    has_health_data = any(
        metrics.get(key) not in (None, {}, "")
        for key in ["sleep_minutes", "sleep_stages", "resting_heart_rate", "latest_heart_rate", "avg_heart_rate"]
    )
    if has_health_data:
        return metrics
    return {}


def build_health_advice(metrics: Optional[Dict[str, Any]], warnings: List[str]) -> str:
    if not metrics:
        return "暂未获取到可用手环健康指标，本次建议仅基于症状描述和本地知识库。"

    parts = ["手环睡眠和心率数据仅作为健康参考，不能替代医生诊断或专业医疗设备检测。"]
    sleep_minutes = metrics.get("sleep_minutes")
    if isinstance(sleep_minutes, int):
        hours = sleep_minutes / 60
        if sleep_minutes < 360:
            parts.append(f"最近一次睡眠约 {hours:.1f} 小时，偏少，建议注意休息、补水并观察症状变化。")
        else:
            parts.append(f"最近一次睡眠约 {hours:.1f} 小时，可作为当前状态参考。")

    resting_hr = metrics.get("resting_heart_rate")
    latest_hr = metrics.get("latest_heart_rate") or metrics.get("avg_heart_rate")
    if isinstance(resting_hr, (int, float)):
        parts.append(f"静息心率约 {int(resting_hr)} 次/分。")
        if resting_hr >= 100:
            parts.append("静息心率偏高，如同时出现发热、胸闷、气促或心悸，应尽快线下就医。")
    elif isinstance(latest_hr, (int, float)):
        parts.append(f"近期心率约 {int(latest_hr)} 次/分。")

    if metrics.get("spo2") is None:
        parts.append("当前 Fitbit Inspire HR 不支持血氧数据，因此不判断血氧正常或异常。")
    if warnings:
        parts.append("部分健康数据可能缺失或同步不及时。")
    return "".join(parts)


def _data_points(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    points = payload.get("dataPoints") or payload.get("data_points") or payload.get("rollupDataPoints") or []
    if isinstance(points, list):
        return [point for point in points if isinstance(point, dict)]
    return []


def _latest_sleep_summary(points: List[Dict[str, Any]]) -> Dict[str, Any]:
    best: Dict[str, Any] = {}
    for point in points:
        sleep = point.get("sleep", point)
        summary = sleep.get("summary", {}) if isinstance(sleep, dict) else {}
        if not isinstance(summary, dict):
            continue
        best = summary
    if not best:
        return {"sleep_minutes": None, "sleep_stages": {}}

    stages = {}
    for item in best.get("stagesSummary", []) or []:
        if not isinstance(item, dict):
            continue
        stage_type = str(item.get("type", "")).lower()
        minutes = _safe_int(item.get("minutes"))
        if stage_type and minutes is not None:
            stages[f"{stage_type}_minutes"] = minutes

    return {
        "sleep_minutes": _safe_int(best.get("minutesAsleep") or best.get("minutesInSleepPeriod")),
        "sleep_stages": stages,
    }


def _latest_measurement(points: List[Dict[str, Any]], keys: Iterable[str]) -> Optional[int]:
    values = _extract_numeric_values(points, keys)
    if not values:
        return None
    return int(round(values[-1]))


def _average_measurement(points: List[Dict[str, Any]], keys: Iterable[str]) -> Optional[int]:
    values = _extract_numeric_values(points, keys)
    if not values:
        return None
    return int(round(sum(values) / len(values)))


def _extract_numeric_values(values: Any, keys: Iterable[str]) -> List[float]:
    wanted = set(keys)
    found: List[float] = []
    if isinstance(values, dict):
        for key, value in values.items():
            if key in wanted:
                number = _safe_float(value)
                if number is not None:
                    found.append(number)
            found.extend(_extract_numeric_values(value, wanted))
    elif isinstance(values, list):
        for item in values:
            found.extend(_extract_numeric_values(item, wanted))
    return found


def _latest_timestamp(points: List[Dict[str, Any]]) -> str:
    candidates = []
    for point in points:
        for key in ["updateTime", "endTime", "sampleTime", "createTime"]:
            value = point.get(key)
            if isinstance(value, str):
                candidates.append(value)
            elif isinstance(value, dict):
                physical_time = value.get("physicalTime")
                if isinstance(physical_time, str):
                    candidates.append(physical_time)
    return sorted(candidates)[-1] if candidates else ""


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
