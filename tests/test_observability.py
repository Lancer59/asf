import logging
import logging.handlers

from observability import (
    RequestIdFilter,
    RequestLoggingMiddleware,
    configure_logging,
    LOG_BACKUP_COUNT,
    LOG_MAX_BYTES,
    request_id_var,
)


def _run_immediate_coroutine(coroutine):
    """Drive middleware with in-memory ASGI doubles that never suspend."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("The in-memory ASGI test unexpectedly suspended")


def test_configured_logging_persists_request_id_and_structured_event(tmp_path, monkeypatch):
    logger = logging.getLogger("asf")
    old_handlers = logger.handlers[:]
    old_level = logger.level
    old_propagate = logger.propagate
    for handler in old_handlers:
        logger.removeHandler(handler)
    try:
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("ASF_LOG_FILE", "logs/test.log")
        configure_logging(tmp_path)
        token = request_id_var.set("request-123")
        try:
            logging.getLogger("asf.agent").info("agent_tool_finished tool=list_approved_skills")
        finally:
            request_id_var.reset(token)

        log_text = (tmp_path / "logs" / "test.log").read_text(encoding="utf-8")
        assert "INFO asf.agent request_id=request-123" in log_text
        assert "agent_tool_finished tool=list_approved_skills" in log_text
    finally:
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.handlers[:] = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate


def test_request_middleware_adds_response_id_and_logs_request_lifecycle():
    records = []
    logger = logging.getLogger("asf")

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    capture = CaptureHandler()
    capture.addFilter(RequestIdFilter())
    logger.addHandler(capture)
    response_messages = []
    observed_id = []

    async def app(scope, receive, send):
        observed_id.append(request_id_var.get())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"chunk", "more_body": True})
        await send({"type": "http.response.body", "body": b"done", "more_body": False})

    async def send(message):
        response_messages.append(message)

    scope = {
        "type": "http", "method": "POST", "path": "/api/chat/stream",
        "query_string": b"secret=must-not-log", "headers": [],
    }
    try:
        _run_immediate_coroutine(RequestLoggingMiddleware(app)(scope, None, send))
    finally:
        logger.removeHandler(capture)

    request_id = scope["state"]["request_id"]
    assert observed_id == [request_id]
    response_headers = dict(response_messages[0]["headers"])
    assert response_headers[b"x-request-id"].decode() == request_id
    assert [record.getMessage().split()[0] for record in records] == [
        "http_request_started", "http_request_finished"
    ]
    assert all(record.request_id == request_id for record in records)
    assert all("must-not-log" not in record.getMessage() for record in records)


def test_rotating_log_configuration_uses_twenty_mib_and_five_backups():
    assert LOG_MAX_BYTES == 20 * 1024 * 1024
    assert LOG_BACKUP_COUNT == 5


def test_configured_file_handler_rotates_logs(tmp_path, monkeypatch):
    logger = logging.getLogger("asf")
    old_handlers = logger.handlers[:]
    for handler in old_handlers:
        logger.removeHandler(handler)
    try:
        monkeypatch.setenv("ASF_LOG_FILE", "logs/rotate.log")
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        configure_logging(tmp_path)
        handler = next(item for item in logger.handlers if isinstance(item, logging.handlers.RotatingFileHandler))
        assert handler.maxBytes == 20 * 1024 * 1024
        assert handler.backupCount == 5
    finally:
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.handlers[:] = old_handlers
