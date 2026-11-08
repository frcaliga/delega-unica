"""Registro cronologico delle deleghe e dati dello studio (intermediario e utenti)."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Role, Tenant, User
from app.routers import registro as api
from app.routers import tenant as api_tenant
from app.routers import users as api_users
from app.schemas import UserCreate
from app.schemas.deleghe import TenantUpdate
from app.services import registro
from app.web.base import errori_da, pagina, vai, vuoto_a_none
from app.web.sessione import richiedi_ruolo, utente_web

router = APIRouter()

RESPONSABILI = (Role.ADMIN, Role.RESPONSABILE_DELEGHE)


def _data(valore: str) -> date | None:
    try:
        return date.fromisoformat(valore) if valore else None
    except ValueError:
        return None


@router.get("/registro", response_class=HTMLResponse)
def elenco(
    request: Request,
    dal: str = "",
    al: str = "",
    user: User = Depends(utente_web),
    db: Session = Depends(get_db),
) -> Response:
    voci = api.list_registro(dal=_data(dal), al=_data(al), user=user, db=db)
    contesto = {
        "voci": voci,
        "dal": dal,
        "al": al,
        "tipi": api.TIPI,
        "puo_esportare": user.role in RESPONSABILI,
        "descrizione_servizi": registro.descrizione_servizi,
    }
    return pagina(request, "registro.html", contesto, user=user)


@router.get("/registro/export.csv")
def esporta(
    dal: str = "", al: str = "", user: User = Depends(utente_web), db: Session = Depends(get_db)
) -> Response:
    richiedi_ruolo(user, *RESPONSABILI)
    return api.export_registro(dal=_data(dal), al=_data(al), user=user, db=db)


@router.post("/registro/verifica", response_class=HTMLResponse)
def verifica(
    request: Request, user: User = Depends(utente_web), db: Session = Depends(get_db)
) -> Response:
    richiedi_ruolo(user, *RESPONSABILI)
    esito = registro.verifica(db, user.tenant_id)
    return pagina(request, "_verifica_registro.html", {"esito": esito}, user=user)


def _pagina_studio(request: Request, user: User, db: Session, *, status_code=200, **extra):
    utenti = []
    if user.role in RESPONSABILI:
        utenti = api_users.list_users(current=user, db=db)
    contesto = {
        "tenant": db.get_one(Tenant, user.tenant_id),
        "utenti": utenti,
        "dati_utente": {},
        **extra,
    }
    return pagina(
        request, "studio.html", contesto, user=user, status_code=status_code, url="/studio/studio"
    )


@router.get("/studio", response_class=HTMLResponse)
def studio(
    request: Request, user: User = Depends(utente_web), db: Session = Depends(get_db)
) -> Response:
    return _pagina_studio(request, user, db)


@router.post("/studio", response_class=HTMLResponse)
async def aggiorna_studio(
    request: Request, user: User = Depends(utente_web), db: Session = Depends(get_db)
) -> Response:
    richiedi_ruolo(user, Role.ADMIN)
    form = await request.form()
    dati = {
        "name": vuoto_a_none(form.get("name")),
        "cognome": vuoto_a_none(form.get("cognome")),
        "nome": vuoto_a_none(form.get("nome")),
    }
    try:
        if not dati["name"]:
            raise HTTPException(422, detail="Indicare la denominazione dello studio")
        api_tenant.update_tenant(TenantUpdate(**dati), request=request, user=user, db=db)
    except (ValidationError, HTTPException) as exc:
        db.rollback()
        return _pagina_studio(request, user, db, status_code=422, errori_studio=errori_da(exc))
    return vai("/studio/studio", ok="studio")


@router.post("/studio/utenti", response_class=HTMLResponse)
async def crea_utente(
    request: Request, user: User = Depends(utente_web), db: Session = Depends(get_db)
) -> Response:
    richiedi_ruolo(user, Role.ADMIN)
    form = await request.form()
    dati = {
        "email": vuoto_a_none(form.get("email")),
        "full_name": vuoto_a_none(form.get("full_name")),
        "role": vuoto_a_none(form.get("role")) or Role.OPERATORE.value,
    }
    try:
        body = UserCreate(**dati, password=str(form.get("password") or ""))
        api_users.create_user(body, request=request, current=user, db=db)
    except (ValidationError, HTTPException) as exc:
        db.rollback()
        return _pagina_studio(
            request, user, db, status_code=422, dati_utente=dati, errori_utente=errori_da(exc)
        )
    return vai("/studio/studio", ok="utente")
