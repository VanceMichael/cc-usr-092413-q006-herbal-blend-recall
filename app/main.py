from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .errors import DomainError
from .routes import router
from .store import Store


def create_app() -> FastAPI:
    app = FastAPI(title="本草溯源")
    app.state.store = Store()

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.include_router(router)
    return app


app = create_app()
