"""Structured, privacy-conscious logging for the local ASF application."""
from __future__ import annotations

import contextvars
import logging
import logging.handlers
import os
import time
import uuid
from pathlib import Path


request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("asf_request_id", default="-")
_LOG_FORMAT = "%(asctime)sZ %(levelname)s %(name)s request_id=%(request_id)s %(message)s"
LOG_MAX_BYTES = 20 * 1024 * 1024
LOG_BACKUP_COUNT = 5


def configured_log_path(project_root: Path | None = None) -> Path | None:
    """Resolve the configured log path, returning None when file logging is disabled."""
    value = os.getenv("ASF_LOG_FILE", "data/logs/asf.log").strip()
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return (project_root or Path(__file__).resolve().parent) / path


def available_log_files(project_root: Path | None = None) -> list[Path]:
    """List only the active log and numbered files managed by RotatingFileHandler."""
    base = configured_log_path(project_root)
    if base is None:
        return []
    candidates = []
    if base.is_file():
        candidates.append(base)
    for candidate in base.parent.glob(base.name + ".*"):
        suffix = candidate.name[len(base.name) + 1:]
        if candidate.is_file() and suffix.isdigit() and 1 <= int(suffix) <= LOG_BACKUP_COUNT:
            candidates.append(candidate)
    return sorted(
        candidates,
        key=lambda path: (path.stat().st_mtime_ns, path == base),
        reverse=True,
    )


class RequestIdFilter(logging.Filter):
    """Attach the current HTTP request ID to every ASF log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(project_root: Path | None = None) -> logging.Logger:
    """Configure stdout plus a small rotating local log file; safe to call repeatedly."""
    root = project_root or Path(__file__).resolve().parent
    logger = logging.getLogger("asf")
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    logger.propagate = False

    # Replace only our own handlers, leaving any embedding application's
    # handlers intact while allowing configuration to be refreshed in tests.
    for handler in tuple(logger.handlers):
        if getattr(handler, "_asf_managed", False):
            logger.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(_LOG_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(RequestIdFilter())
    console._asf_managed = True
    logger.addHandler(console)

    configured_path = os.getenv("ASF_LOG_FILE", "data/logs/asf.log").strip()
    if configured_path:
        log_path = Path(configured_path)
        if not log_path.is_absolute():
            log_path = root / log_path
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            file_handler.addFilter(RequestIdFilter())
            file_handler._asf_managed = True
            logger.addHandler(file_handler)
        except OSError as exc:
            logger.warning("file_logging_unavailable error_type=%s", type(exc).__name__)
    return logger


def current_request_id() -> str:
    return request_id_var.get()


class RequestLoggingMiddleware:
    """Pure ASGI request context and duration logging, including streamed bodies."""

    def __init__(self, app):
        self.app = app
        self.logger = logging.getLogger("asf.http")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        token = request_id_var.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status_code = 500
        method = scope.get("method", "-")
        # Deliberately omit query strings and request bodies from access logs.
        path = scope.get("path", "-")
        self.logger.info("http_request_started method=%s path=%s", method, path)

        async def send_with_request_id(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            self.logger.error(
                "http_request_failed method=%s path=%s error_type=%s",
                method, path, type(exc).__name__,
            )
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.logger.info(
                "http_request_finished method=%s path=%s status=%s duration_ms=%s",
                method, path, status_code, duration_ms,
            )
            request_id_var.reset(token)
