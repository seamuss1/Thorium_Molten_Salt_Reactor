from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, Request

from thorium_reactor.web.schemas import AuthSession, RateLimitRecord


OWNER_EMAIL = "seamusdgallagher@gmail.com"
LOCAL_DEV_EMAIL = OWNER_EMAIL
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}
ACCESS_EMAIL_HEADERS = (
    "cf-access-authenticated-user-email",
    "x-authenticated-user-email",
    "x-forwarded-email",
    "x-user-email",
)


@dataclass(frozen=True)
class AccessUser:
    email: str
    is_admin: bool


class AccessController:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.admin_emails = configured_admin_emails()
        self.daily_limit = configured_daily_limit()
        self.access_required = truthy(os.environ.get("THORIUM_REACTOR_ACCESS_REQUIRED"))
        self.store = RateLimitStore(repo_root, daily_limit=self.daily_limit)

    def user_from_request(self, request: Request) -> AccessUser:
        email = email_from_headers(request)
        if email is None:
            if self.access_required and not is_localhost_request(request):
                raise HTTPException(status_code=401, detail="Cloudflare Access identity is required to start simulations.")
            email = normalize_email(os.environ.get("THORIUM_REACTOR_LOCAL_DEV_EMAIL", LOCAL_DEV_EMAIL))
        return AccessUser(email=email, is_admin=email in self.admin_emails)

    def session_for(self, user: AccessUser) -> AuthSession:
        record = self.store.record_for(user.email)
        return AuthSession(
            email=user.email,
            is_admin=user.is_admin,
            admin_emails=sorted(self.admin_emails),
            daily_run_limit=None if user.is_admin else self.daily_limit,
            runs_started_today=0 if user.is_admin else record.count,
            runs_remaining_today=None if user.is_admin else max(self.daily_limit - record.count, 0),
            rate_limit_date=record.date,
            resets_at=record.resets_at,
            can_start_run=user.is_admin or record.count < self.daily_limit,
        )

    def claim_run_start(self, user: AccessUser) -> RateLimitRecord | None:
        if user.is_admin:
            return None
        return self.store.claim(user.email)

    def release_run_start(self, user: AccessUser) -> None:
        if not user.is_admin:
            self.store.release(user.email)

    def require_admin(self, user: AccessUser) -> AccessUser:
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="Administrator access is required.")
        return user


class RateLimitStore:
    def __init__(self, repo_root: Path, *, daily_limit: int) -> None:
        self.path = configured_store_path(repo_root)
        self.daily_limit = daily_limit
        self.zone = configured_timezone()
        self._lock = threading.Lock()

    def record_for(self, email: str) -> RateLimitRecord:
        with self._lock:
            data = self._read()
            return self._record_from_payload(email, data.get("users", {}).get(email, {}))

    def claim(self, email: str) -> RateLimitRecord:
        with self._lock:
            data = self._read()
            users = data.setdefault("users", {})
            record = self._record_from_payload(email, users.get(email, {}))
            if record.count >= self.daily_limit:
                raise HTTPException(
                    status_code=429,
                    detail=f"Daily simulation start limit reached for {email}. Ask an admin to reset it.",
                )
            now = utc_timestamp()
            payload = record.model_dump() if hasattr(record, "model_dump") else record.dict()
            payload.update(
                {
                    "count": record.count + 1,
                    "last_started_at": now,
                    "limit": self.daily_limit,
                    "resets_at": self._resets_at(),
                }
            )
            users[email] = payload
            self._write(data)
            return self._record_from_payload(email, payload)

    def release(self, email: str) -> None:
        with self._lock:
            data = self._read()
            users = data.setdefault("users", {})
            record = self._record_from_payload(email, users.get(email, {}))
            payload = record.model_dump() if hasattr(record, "model_dump") else record.dict()
            payload["count"] = max(record.count - 1, 0)
            users[email] = payload
            self._write(data)

    def reset(self, email: str, *, reset_by: str) -> RateLimitRecord:
        normalized = normalize_email(email)
        with self._lock:
            data = self._read()
            users = data.setdefault("users", {})
            payload = {
                "email": normalized,
                "date": self._date_key(),
                "count": 0,
                "limit": self.daily_limit,
                "remaining": self.daily_limit,
                "last_started_at": None,
                "last_reset_at": utc_timestamp(),
                "reset_by": reset_by,
                "resets_at": self._resets_at(),
            }
            users[normalized] = payload
            self._write(data)
            return self._record_from_payload(normalized, payload)

    def list_records(self) -> list[RateLimitRecord]:
        with self._lock:
            data = self._read()
            records = [self._record_from_payload(email, payload) for email, payload in data.get("users", {}).items()]
        return sorted(records, key=lambda record: record.email)

    def _record_from_payload(self, email: str, payload: dict[str, Any]) -> RateLimitRecord:
        date_key = self._date_key()
        count = int(payload.get("count", 0)) if payload.get("date") == date_key else 0
        return RateLimitRecord(
            email=normalize_email(payload.get("email", email)),
            date=date_key,
            count=count,
            limit=self.daily_limit,
            remaining=max(self.daily_limit - count, 0),
            last_started_at=payload.get("last_started_at") if count else None,
            last_reset_at=payload.get("last_reset_at"),
            reset_by=payload.get("reset_by"),
            resets_at=self._resets_at(),
        )

    def _date_key(self) -> str:
        return datetime.now(UTC).astimezone(self.zone).date().isoformat()

    def _resets_at(self) -> str:
        now = datetime.now(UTC).astimezone(self.zone)
        tomorrow = now.date() + timedelta(days=1)
        reset = datetime.combine(tomorrow, time.min, tzinfo=self.zone).astimezone(UTC)
        return timestamp(reset)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"users": {}}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"users": {}}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(self.path)


def email_from_headers(request: Request) -> str | None:
    for header in ACCESS_EMAIL_HEADERS:
        value = request.headers.get(header)
        if value:
            return normalize_email(value)
    return None


def is_localhost_request(request: Request) -> bool:
    host = request.headers.get("host", "")
    hostname = host.rsplit(":", 1)[0].strip("[]").lower()
    return hostname in LOCAL_HOSTNAMES


def configured_admin_emails() -> set[str]:
    emails = {OWNER_EMAIL}
    raw = os.environ.get("THORIUM_REACTOR_ADMIN_EMAILS", "")
    for value in raw.replace(";", ",").split(","):
        value = value.strip()
        if value:
            emails.add(normalize_email(value))
    return emails


def configured_daily_limit() -> int:
    raw = os.environ.get("THORIUM_REACTOR_RATE_LIMIT_PER_DAY", "1")
    try:
        return max(int(raw), 1)
    except ValueError:
        return 1


def configured_store_path(repo_root: Path) -> Path:
    raw = os.environ.get("THORIUM_REACTOR_RATE_LIMIT_PATH")
    if raw:
        return Path(raw)
    return repo_root / ".tmp" / "web-rate-limits.json"


def configured_timezone() -> ZoneInfo:
    raw = os.environ.get("THORIUM_REACTOR_RATE_LIMIT_TIMEZONE", "America/New_York")
    try:
        return ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def normalize_email(email: Any) -> str:
    normalized = str(email).strip().lower()
    if "@" not in normalized:
        raise HTTPException(status_code=400, detail="A valid email address is required.")
    return normalized


def truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def utc_timestamp() -> str:
    return timestamp(datetime.now(UTC))


def timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
