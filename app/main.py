from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import build_router
from app.domain.errors import (
    ApprovalError,
    ConservationViolation,
    DomainError,
    IdempotencyConflict,
    InsufficientQuantity,
    NotFoundError,
    StateError,
    VersionConflict,
)
from app.services import build_state


def create_app() -> FastAPI:
    app = FastAPI(title="本草溯源")
    state = build_state()
    app.state.domain = state
    app.include_router(build_router(state), prefix="/api")

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(IdempotencyConflict)
    async def idempotency_conflict(_: Request, exc: IdempotencyConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "quarantine_id": exc.quarantine_id},
        )

    @app.exception_handler(VersionConflict)
    async def version_conflict(_: Request, exc: VersionConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(InsufficientQuantity)
    async def insufficient(_: Request, exc: InsufficientQuantity) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(StateError)
    async def state_error(_: Request, exc: StateError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ConservationViolation)
    async def conservation(_: Request, exc: ConservationViolation) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ApprovalError)
    async def approval(_: Request, exc: ApprovalError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(DomainError)
    async def domain(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
