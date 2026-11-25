"""The contract's one error body (section 7), for every error the API answers."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from legoin_api.schemas import CHANGE_TYPES, ApiErrorBody, ErrorCode, ErrorDetail

log = logging.getLogger(__name__)

STATUS: dict[ErrorCode, int] = {
    "invalid_request": 400,
    "not_found": 404,
    "no_pieces": 409,
    "photo_too_large": 413,
    "photo_type": 415,
    "timeout": 504,
    "unavailable": 503,
    "internal": 500,
    "unauthorized": 401,
    "invalid_credentials": 401,
    "email_taken": 409,
    "rate_limited": 429,
}


class ApiError(Exception):
    """Raised anywhere in a request to answer with the contract's error body."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        field: str | None = None,
        status: int | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.detail = ErrorDetail(code=code, message=message, field=field)
        self.status = status or STATUS.get(code, 500)
        self.headers = headers


def error_response(
    code: ErrorCode,
    message: str,
    *,
    field: str | None = None,
    status: int | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ApiErrorBody(error=ErrorDetail(code=code, message=message, field=field))
    return JSONResponse(body.wire(), status_code=status or STATUS[code], headers=headers)


def field_path(loc: tuple) -> str:
    """A pydantic error location as the contract writes it, e.g. `changes[1].quantity`.

    Drops where the value came from (`body`, `header`, ...) and the tag pydantic adds for a
    detection change's `type`, which isn't a field the client sent.
    """
    parts = list(loc)
    if parts and parts[0] in ("body", "query", "path", "header", "cookie"):
        parts = parts[1:]
    path = ""
    previous = None
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        elif not (isinstance(previous, int) and part in CHANGE_TYPES):
            path += f".{part}" if path else str(part)
        previous = part
    return path


async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        ApiErrorBody(error=exc.detail).wire(), status_code=exc.status, headers=exc.headers
    )


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0]
    field = field_path(tuple(first.get("loc", ())))
    if first.get("type") == "json_invalid":
        return error_response("invalid_request", "The body isn't valid JSON.")
    message = f"{field}: {first.get('msg')}" if field else str(first.get("msg"))
    return error_response("invalid_request", message, field=field or None)


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 404:
        return error_response("not_found", f"No route {request.url.path}.")
    if exc.status_code == 405:
        return error_response(
            "invalid_request", f"{request.method} isn't allowed on {request.url.path}.", status=405
        )
    code: ErrorCode = "invalid_request" if exc.status_code < 500 else "internal"
    return error_response(code, str(exc.detail), status=exc.status_code)


class CatchAll:
    """Answers anything unexpected with `500 internal`.

    It sits inside the CORS middleware, unlike Starlette's own server-error handler, so a
    browser can still read the answer.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_wrapper(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            log.exception("Unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if started:
                raise
            response = error_response("internal", "Something went wrong on the server.")
            await response(scope, receive, send)


def install(app: FastAPI) -> None:
    """Adds the handlers. Call it before adding the CORS middleware, so CORS wraps them."""
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_middleware(CatchAll)
