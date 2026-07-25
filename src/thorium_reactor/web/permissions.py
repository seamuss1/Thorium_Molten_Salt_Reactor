from __future__ import annotations

import contextlib
import hmac
import ipaddress
import json
import os
import threading
import time as time_module
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, Request

from thorium_reactor.web.schemas import AuthSession, RateLimitRecord

if os.name == "nt":
    import msvcrt
else:
    import fcntl


OWNER_EMAIL = "seamusdgallagher@gmail.com"
LOCAL_DEV_EMAIL = OWNER_EMAIL
ACCESS_IDENTITY_HEADER = "cf-access-authenticated-user-email"
PROXY_SECRET_HEADER = "x-thorium-proxy-secret"


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
        self.proxy_secret = configured_proxy_secret()
        self.trusted_client_networks = configured_trusted_client_networks()
        self.store = RateLimitStore(repo_root, daily_limit=self.daily_limit)

    def user_from_request(self, request: Request) -> AccessUser:
        email = self.verified_email_from_headers(request)
        if email is None:
            if self.access_required and not self.is_trusted_local_transport(request):
                raise HTTPException(
                    status_code=401,
                    detail="A verified access identity is required to use this deployment.",
                )
            email = normalize_email(os.environ.get("THORIUM_REACTOR_LOCAL_DEV_EMAIL", LOCAL_DEV_EMAIL))
        return AccessUser(email=email, is_admin=email in self.admin_emails)

    def verified_email_from_headers(self, request: Request) -> str | None:
        raw = request.headers.get(ACCESS_IDENTITY_HEADER)
        if not raw:
            return None
        if not self.access_required:
            # Development convenience only: without the access requirement the
            # deployment is already treated as a trusted local lab.
            return normalize_email(raw)
        if self.proxy_secret is None:
            # Fail closed: without a proxy shared secret there is no way to
            # prove the identity header came from the trusted access proxy.
            return None
        presented = request.headers.get(PROXY_SECRET_HEADER, "")
        if not hmac.compare_digest(presented.encode("utf-8"), self.proxy_secret.encode("utf-8")):
            return None
        return normalize_email(raw)

    def is_trusted_local_transport(self, request: Request) -> bool:
        client = request.client
        if client is None or not client.host:
            return False
        try:
            address = ipaddress.ip_address(client.host)
        except ValueError:
            return False
        if address.is_loopback:
            return True
        return any(address in network for network in self.trusted_client_networks)

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
        with self._exclusive():
            data = self._read()
            return self._record_from_payload(email, data.get("users", {}).get(email, {}))

    def claim(self, email: str) -> RateLimitRecord:
        with self._exclusive():
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
        with self._exclusive():
            data = self._read()
            users = data.setdefault("users", {})
            record = self._record_from_payload(email, users.get(email, {}))
            payload = record.model_dump() if hasattr(record, "model_dump") else record.dict()
            payload["count"] = max(record.count - 1, 0)
            users[email] = payload
            self._write(data)

    def reset(self, email: str, *, reset_by: str) -> RateLimitRecord:
        normalized = normalize_email(email)
        with self._exclusive():
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
        with self._exclusive():
            data = self._read()
            records = [self._record_from_payload(email, payload) for email, payload in data.get("users", {}).items()]
        return sorted(records, key=lambda record: record.email)

    @contextlib.contextmanager
    def _exclusive(self) -> Iterator[None]:
        """Serialize read-modify-write cycles across threads and processes."""
        with self._lock:
            lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+b") as handle:
                _lock_file_exclusive(handle)
                try:
                    yield
                finally:
                    _unlock_file(handle)

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


def _lock_file_exclusive(handle: Any) -> None:
    if os.name == "nt":
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time_module.sleep(0.01)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: Any) -> None:
    if os.name == "nt":
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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


def configured_proxy_secret() -> str | None:
    raw = os.environ.get("THORIUM_REACTOR_PROXY_SHARED_SECRET", "").strip()
    return raw or None


def configured_trusted_client_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    raw = os.environ.get("THORIUM_REACTOR_TRUSTED_CLIENT_ADDRS", "")
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for value in raw.replace(";", ",").split(","):
        value = value.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise ValueError(
                f"THORIUM_REACTOR_TRUSTED_CLIENT_ADDRS entry '{value}' is not a valid IP address or CIDR network."
            ) from exc
    return networks


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
