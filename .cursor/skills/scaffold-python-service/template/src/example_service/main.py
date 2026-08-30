"""Typed FastAPI entrypoint.

Business logic stays out of this module — it's the I/O/framework layer per the
Python rules. Real logic belongs in plain, pure functions elsewhere in the package
that this layer calls and translates into HTTP.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from example_service.errors import NotFoundError, ServiceError, ValidationError


def create_app() -> FastAPI:
    """App factory — keeps import-time side effects out of module scope, so tests
    can construct a fresh app per test rather than sharing global state."""
    app = FastAPI(title="example-service")

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def _invalid(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
        # Catch-all for expected-but-unmapped ServiceError subclasses. A genuine bug
        # (anything not deriving from ServiceError) is deliberately left to propagate
        # rather than being caught here — see errors.py.
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
