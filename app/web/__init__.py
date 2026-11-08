"""Interfaccia web per gli operatori dello studio, servita dalla stessa applicazione delle API.

Pagine Jinja2 rese dal server con HTMX per le parti dinamiche: nessun frontend separato. Le
pagine richiamano le stesse funzioni delle API, quindi regole, controlli e audit restano unici.
"""

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.web import auth, clienti, deleghe, documenti, fea, registro
from app.web.base import CARTELLA, is_htmx, pagina
from app.web.sessione import (
    COOKIE_CSRF,
    COOKIE_SESSIONE,
    PREFISSO,
    NonAutenticato,
    imposta_cookie,
    verifica_csrf,
)

# Le pagine non usano script o stili in linea: la CSP lo vieta
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def _nella_web(request: Request) -> bool:
    path = request.url.path
    return path == PREFISSO or path.startswith(PREFISSO + "/")


def installa(app: FastAPI) -> None:
    for modulo in (auth, deleghe, documenti, clienti, fea, registro):
        app.include_router(
            modulo.router,
            prefix=PREFISSO,
            dependencies=[Depends(verifica_csrf)],
            include_in_schema=False,
        )
    app.mount(f"{PREFISSO}/static", StaticFiles(directory=CARTELLA / "static"), name="static")

    @app.get("/", include_in_schema=False)
    def radice() -> RedirectResponse:
        return RedirectResponse(PREFISSO, status_code=307)

    @app.exception_handler(NonAutenticato)
    async def _non_autenticato(request: Request, exc: NonAutenticato) -> Response:
        destinazione = f"{PREFISSO}/accesso"
        if is_htmx(request):
            return Response(status_code=204, headers={"HX-Redirect": destinazione})
        return RedirectResponse(destinazione, status_code=303)

    @app.exception_handler(StarletteHTTPException)
    async def _errore_http(request: Request, exc: StarletteHTTPException) -> Response:
        if not _nella_web(request):
            return await http_exception_handler(request, exc)
        dettaglio = exc.detail if isinstance(exc.detail, str) else "Richiesta non valida"
        return pagina(
            request,
            "errore.html",
            {"codice": exc.status_code, "dettaglio": dettaglio},
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def _errore_validazione(request: Request, exc: RequestValidationError) -> Response:
        if not _nella_web(request):
            return await request_validation_exception_handler(request, exc)
        return pagina(
            request,
            "errore.html",
            {"codice": 404, "dettaglio": "Pagina non trovata"},
            status_code=404,
        )

    @app.middleware("http")
    async def _intestazioni(request: Request, call_next) -> Response:
        response = await call_next(request)
        if not _nella_web(request):
            return response
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.startswith(f"{PREFISSO}/static"):
            response.headers.setdefault("Cache-Control", "no-store")
        token = getattr(request.state, "rinnova_sessione", None)
        if token and "set-cookie" not in response.headers:
            minuti = get_settings().access_token_minutes
            imposta_cookie(response, COOKIE_SESSIONE, token, minuti)
        if csrf := getattr(request.state, "nuovo_csrf", None):
            # Valido per una giornata di lavoro, poi se ne crea uno nuovo
            imposta_cookie(response, COOKIE_CSRF, csrf, 12 * 60)
        return response
