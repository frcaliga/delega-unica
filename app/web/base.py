"""Strumenti comuni alle pagine: template Jinja2, filtri e messaggi d'errore."""

from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.clock import ROMA
from app.domain.regole import (
    DESCRIZIONE_SERVIZI,
    ModalitaFirma,
    Qualifica,
    Servizio,
    TipoDocumento,
    TipoRichiesta,
)
from app.models import Role, StatoAdesione, StatoDelega, User
from app.web.sessione import CAMPO_CSRF, HEADER_CSRF, token_csrf

CARTELLA = Path(__file__).parent
templates = Jinja2Templates(directory=CARTELLA / "templates")

STATI = {
    StatoDelega.BOZZA: "Bozza",
    StatoDelega.IN_FIRMA: "In firma",
    StatoDelega.FIRMATA: "Firmata",
    StatoDelega.INVIATA: "Inviata",
    StatoDelega.ATTIVA: "Attiva",
    StatoDelega.ACCETTATA: "Revoca accettata",
    StatoDelega.SCARTATA: "Scartata",
    StatoDelega.SOSTITUITA: "Sostituita",
    StatoDelega.REVOCATA: "Revocata",
}
TIPI_RICHIESTA = {
    TipoRichiesta.CONFERIMENTO: "Conferimento",
    TipoRichiesta.REVOCA: "Revoca",
    TipoRichiesta.RINNOVO: "Rinnovo",
}
MODALITA_FIRMA = {
    ModalitaFirma.FIRMA_DIGITALE: "Firma digitale",
    ModalitaFirma.FEA_CIE: "FEA con CIE",
    ModalitaFirma.FEA_NON_QUALIFICATA: "FEA allo sportello (OTP via SMS)",
}
QUALIFICHE = {
    Qualifica.DELEGANTE: "Delegante",
    Qualifica.TUTORE_GENITORE: "Tutore o genitore",
    Qualifica.EREDE: "Erede",
    Qualifica.RAPPRESENTANTE: "Rappresentante legale",
    Qualifica.CURATORE_FALLIMENTARE: "Curatore fallimentare",
}
TIPI_DOCUMENTO = {
    TipoDocumento.CARTA_IDENTITA: "Carta d'identita'",
    TipoDocumento.PASSAPORTO: "Passaporto",
    TipoDocumento.PATENTE: "Patente",
    TipoDocumento.ALTRO: "Altro",
}
STATI_ADESIONE = {
    StatoAdesione.DA_CONFERMARE: "Da confermare",
    StatoAdesione.ATTIVA: "Attiva",
    StatoAdesione.REVOCATA: "Revocata",
}
SESSI = {"M": "Maschile", "F": "Femminile"}
# Campi letti senza cifre di controllo a confermarli
CAMPI_MRZ = {
    "numero_documento": "numero del documento",
    "data_nascita": "data di nascita",
    "data_scadenza": "data di scadenza",
}
RUOLI = {
    Role.ADMIN: "Amministratore",
    Role.RESPONSABILE_DELEGHE: "Responsabile deleghe",
    Role.OPERATORE: "Operatore",
}
SERVIZI = {s.value: DESCRIZIONE_SERVIZI[s] for s in Servizio}
# Messaggi di conferma dopo un reindirizzamento: la pagina riceve solo la chiave
AVVISI = {
    "cliente": "Cliente salvato.",
    "documento": "Documento d'identita' archiviato (copia cifrata).",
    "documento_eliminato": "Copia del documento d'identita' eliminata.",
    "delega": "Delega creata in bozza.",
    "xml": "File XML generato: la delega e' in firma.",
    "fea": "Il cliente ha firmato: ora controfirmare la busta con la firma digitale dello studio.",
    "inviata": "Delega segnata come inviata all'Agenzia delle Entrate.",
    "esito": "Esito registrato.",
    "adesione": "Adesione confermata: il cliente puo' firmare con l'OTP.",
    "revocata": "Adesione revocata.",
    "studio": "Dati dello studio aggiornati.",
    "utente": "Utente creato: al primo accesso configurera' il secondo fattore.",
}


def avvisi_lettura(proposta) -> list[str]:
    """Messaggi per l'operatore sull'esito della lettura del documento."""
    avvisi = list(proposta.avvisi)
    if proposta.campi_incerti:
        campi = ", ".join(CAMPI_MRZ.get(c, c) for c in proposta.campi_incerti)
        avvisi.append(f"Letti senza cifre di controllo: {campi}. Controllarli sul documento.")
    if proposta.discordanze:
        avvisi.append(
            f"Non tornano con il codice fiscale: {', '.join(proposta.discordanze)}. "
            "Verificare il documento e l'anagrafica."
        )
    return avvisi


def data_it(valore: date | datetime | None) -> str:
    if valore is None:
        return ""
    if isinstance(valore, datetime):
        return valore.astimezone(ROMA).strftime("%d/%m/%Y %H:%M")
    return valore.strftime("%d/%m/%Y")


templates.env.filters["data"] = data_it
templates.env.globals.update(
    STATI=STATI,
    TIPI_RICHIESTA=TIPI_RICHIESTA,
    MODALITA_FIRMA=MODALITA_FIRMA,
    QUALIFICHE=QUALIFICHE,
    TIPI_DOCUMENTO=TIPI_DOCUMENTO,
    STATI_ADESIONE=STATI_ADESIONE,
    RUOLI=RUOLI,
    SESSI=SESSI,
    avvisi_lettura=avvisi_lettura,
    SERVIZI=SERVIZI,
    Role=Role,
    StatoDelega=StatoDelega,
    StatoAdesione=StatoAdesione,
    ModalitaFirma=ModalitaFirma,
    TipoRichiesta=TipoRichiesta,
    CAMPO_CSRF=CAMPO_CSRF,
    HEADER_CSRF=HEADER_CSRF,
)


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def htmx_target(request: Request) -> str | None:
    """Destinazione di una richiesta HTMX parziale (non di una navigazione con hx-boost)."""
    if is_htmx(request) and request.headers.get("HX-Boosted") != "true":
        return request.headers.get("HX-Target")
    return None


def pagina(
    request: Request,
    nome: str,
    contesto: dict[str, Any] | None = None,
    *,
    user: User | None = None,
    status_code: int = 200,
    url: str | None = None,
) -> HTMLResponse:
    """Rende un template. `url` e' l'indirizzo da mostrare nel browser dopo un POST."""
    ctx = {"user": user, "csrf_token": token_csrf(request), "avviso": None}
    if chiave := request.query_params.get("ok"):
        ctx["avviso"] = AVVISI.get(chiave)
    ctx.update(contesto or {})
    response = templates.TemplateResponse(request, nome, ctx, status_code=status_code)
    if url and is_htmx(request):
        response.headers["HX-Replace-Url"] = url
    return response


def vai(url: str, ok: str | None = None) -> RedirectResponse:
    if ok:
        url = f"{url}{'&' if '?' in url else '?'}ok={ok}"
    return RedirectResponse(url, status_code=303)


def _messaggio_validazione(errore: dict) -> str:
    campo = ".".join(str(p) for p in errore.get("loc", ()) if p != "body")
    if errore.get("type") == "missing":
        return f"Campo obbligatorio mancante: {campo}"
    msg = errore.get("msg", "Valore non valido").removeprefix("Value error, ")
    if errore.get("type", "").startswith(("date", "enum", "uuid", "email", "string")):
        return f"{campo}: valore non valido"
    return msg


def errori_da(exc: Exception) -> list[str]:
    """Messaggi per l'operatore da un errore delle API o della validazione dei dati."""
    if isinstance(exc, ValidationError):
        return [_messaggio_validazione(e) for e in exc.errors()]
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "errori" in detail:
            return [str(e) for e in detail["errori"]]
        return [str(detail)]
    raise exc


def vuoto_a_none(valore: Any) -> str | None:
    if isinstance(valore, str):
        valore = valore.strip()
        return valore or None
    return None
