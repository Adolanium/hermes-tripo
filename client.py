"""The HTTP side: Tripo's v3 API envelope, uploads, polling and downloads."""

from __future__ import annotations

import mimetypes
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .plans import Upload

DEFAULT_BASE = "https://openapi.tripo3d.ai/v3"
USER_AGENT = "hermes-tripo/0.4.0"
TERMINAL = frozenset({"success", "failed", "cancelled", "banned", "expired"})
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
MODEL_TYPES = {".glb", ".gltf", ".fbx", ".obj", ".stl"}


class TripoError(Exception):
    """An error worth showing the model as-is. ``code`` is Tripo's error code,
    ``status`` the HTTP status; both are None when no answer came back."""

    def __init__(self, message: str, *, code: int | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


def _secret(name: str) -> str:
    try:
        from agent.secret_scope import get_secret
    except ImportError:  # outside Hermes (tests, scripts)
        return os.environ.get(name, "")
    try:
        return get_secret(name) or ""
    except Exception as exc:  # e.g. no profile scope while multiplexing: fail closed, but cleanly
        raise TripoError(f"Could not read {name}: {exc}") from exc


def tripo_home() -> Path:
    """``<HERMES_HOME>/tripo``: default downloads and the viewer cache."""
    try:
        from hermes_constants import get_hermes_home
    except ImportError:  # outside Hermes (tests, scripts)
        return Path(os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")) / "tripo"
    return Path(get_hermes_home()) / "tripo"


def working_folder() -> Path:
    """The agent's working folder (where a relative path in a chat points), else the process cwd."""
    try:
        from agent.runtime_cwd import resolve_agent_cwd
    except ImportError:  # outside Hermes (tests, scripts)
        return Path.cwd()
    try:
        return Path(resolve_agent_cwd())
    except OSError:
        return Path.cwd()


def local_path(raw: str) -> Path:
    """``~`` expanded, and a relative path resolved against the agent's working folder: Desktop
    hands file attachments to the agent as workspace-relative ``@file:`` references."""
    path = Path(os.path.expanduser(raw.strip()))
    return path if path.is_absolute() else working_folder() / path


def free_stem(folder: Path, stem: str, suffixes: list[str]) -> str:
    """``stem``, or ``stem (2)``, ``stem (3)``... so that no ``<stem><suffix>`` already exists in ``folder``.
    Files saved together share one number, so a model and its preview keep matching names."""
    candidate, count = stem, 2
    while any((folder / f"{candidate}{suffix}").exists() for suffix in suffixes):
        candidate = f"{stem} ({count})"
        count += 1
    return candidate


def api_key() -> str:
    return _secret("TRIPO_API_KEY").strip()


_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def _loopback_http(url: str) -> bool:
    parts = urlparse(url)
    return parts.scheme == "http" and (parts.hostname or "") in _LOOPBACK


def base_url() -> str:
    """Tripo's API, or ``TRIPO_API_BASE``: https, or plain http on this machine (a local test server)."""
    base = _secret("TRIPO_API_BASE").strip().rstrip("/") or DEFAULT_BASE
    if urlparse(base).scheme != "https" and not _loopback_http(base):
        raise TripoError("TRIPO_API_BASE must be an https URL (or http://127.0.0.1 for a local test server)")
    return base


def _hint(code: int | None, status: int | None) -> str:
    if code in (1000, 1001) or status == 401:
        return " Check TRIPO_API_KEY in the Hermes profile's .env, then start a new session."
    if code == 2010:
        return " The account is out of credits; top up at platform.tripo3d.ai."
    if code in (2000, 1007) or status == 429:
        return " Tripo is rate limiting or the concurrency cap is full; wait for running tasks to finish."
    if code == 2008:
        return " Tripo's content policy refused the input; change the prompt or image."
    return ""


class Client:
    def __init__(
        self, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep
    ):
        key = api_key()
        if not key:
            raise TripoError(
                "TRIPO_API_KEY is not set. Add it to the Hermes profile's .env "
                "(~/.hermes/.env) and start a new session."
            )
        self._http = httpx.Client(
            base_url=base_url(),
            headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT},
            timeout=httpx.Timeout(60.0, connect=15.0),
            transport=transport,
            follow_redirects=False,
        )
        self._downloads = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(300.0, connect=15.0),
            transport=transport,
            follow_redirects=True,
        )
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()
        self._downloads.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- envelope ------------------------------------------------------------

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise TripoError(f"Tripo did not answer in time ({exc.__class__.__name__})") from exc
        except httpx.HTTPError as exc:
            raise TripoError(f"Could not reach Tripo: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise TripoError(
                f"Tripo returned HTTP {response.status_code} without a JSON body", status=response.status_code
            )
        code = payload.get("code")
        if response.is_success and code == 0:
            return payload.get("data")
        message = payload.get("message") or f"HTTP {response.status_code}"
        suggestion = payload.get("suggestion")
        text = f"Tripo error {code}: {message}" + (f" ({suggestion})" if suggestion else "")
        raise TripoError(text + _hint(code, response.status_code), code=code, status=response.status_code)

    def get(self, path: str) -> Any:
        # Reads are safe to retry; creates never are (a retried create can bill twice).
        for attempt in range(3):
            try:
                return self._call("GET", path)
            except TripoError as exc:
                if exc.status != 429 or attempt == 2:
                    raise
                self._sleep(2 ** (attempt + 1))
        raise AssertionError("unreachable")

    def create(self, endpoint: str, body: dict) -> str:
        try:
            data = self._call("POST", endpoint, json=body)
        except TripoError as exc:
            if exc.status is None and exc.code is None:
                raise TripoError(
                    f"{exc} The task may have been created anyway. Check tripo_balance (frozen "
                    "credits) or ask the user before trying again; do not resubmit blindly."
                ) from exc
            raise
        task_id = data.get("task_id") if isinstance(data, dict) else None
        if not task_id:
            raise TripoError(f"Tripo accepted the request but returned no task_id: {data!r}")
        return str(task_id)

    # -- files ---------------------------------------------------------------

    def upload(self, item: Upload) -> str:
        path = local_path(item.path)
        if not path.is_file():
            raise TripoError(f"File not found: {path}")
        allowed = IMAGE_TYPES if item.kind == "image" else MODEL_TYPES
        if path.suffix.lower() not in allowed:
            raise TripoError(f"{path.name}: expected one of {', '.join(sorted(allowed))}")
        size = path.stat().st_size
        if size == 0:
            raise TripoError(f"{path.name} is empty")
        if size > MAX_UPLOAD_BYTES:
            raise TripoError(
                f"{path.name} is {size // 2**20} MB; upload it somewhere public and pass the URL"
            )
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as handle:
            data = self._call(
                "POST",
                "/files",
                files={"file": (path.name, handle, mime)},
                timeout=httpx.Timeout(300.0, connect=15.0),
            )
        token = data.get("file_token") if isinstance(data, dict) else None
        if not token:
            raise TripoError(f"Upload of {path.name} returned no file_token")
        return str(token)

    def resolve(self, value: Any) -> Any:
        """Replace every Upload placeholder in a request body with a file_token."""
        if isinstance(value, Upload):
            return self.upload(value)
        if isinstance(value, dict):
            return {key: self.resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item) for item in value]
        return value

    # -- tasks ---------------------------------------------------------------

    def task(self, task_id: str) -> dict:
        data = self.get(f"/tasks/{task_id}")
        if not isinstance(data, dict):
            raise TripoError(f"Unexpected task payload for {task_id}")
        return data

    def wait(self, task_id: str, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        delay = 2.0
        while True:
            data = self.task(task_id)
            remaining = deadline - time.monotonic()
            if data.get("status") in TERMINAL or remaining <= 0:
                return data
            self._sleep(min(delay, remaining))
            delay = min(delay * 1.5, 10.0)

    def balance(self) -> dict:
        data = self.get("/account/balance")
        return data if isinstance(data, dict) else {}

    def usage(self) -> list:
        data = self.get("/account/usage")
        return data if isinstance(data, list) else []

    def fetch(self, url: str, target: Path) -> int:
        """Stream a signed output URL to ``target``; returns the byte count."""
        # Plain http only when the API itself is a local test server, and only to this machine.
        local = _loopback_http(url) and _loopback_http(str(self._http.base_url))
        if urlparse(url).scheme != "https" and not local:
            raise TripoError(f"Refusing non-https download URL: {url[:80]}")
        partial = target.with_name(target.name + ".part")
        written = 0
        try:
            # Signed CDN URLs must not get the API key, hence the separate client.
            with self._downloads.stream("GET", url) as response:
                if not response.is_success:
                    raise TripoError(
                        f"Download failed with HTTP {response.status_code} "
                        "(output URLs expire; fetch the task again for fresh ones)"
                    )
                with partial.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
                        written += len(chunk)
            os.replace(partial, target)
        except httpx.HTTPError as exc:
            raise TripoError(f"Download failed: {exc}") from exc
        finally:
            if partial.exists():
                partial.unlink()
        return written
