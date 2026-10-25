"""Controlli sul file firmato prima di annotarlo nel registro e trasmetterlo all'AdE.

Riproducono i controlli di struttura e congruenza dell'Allegato 2 (paragrafo 3.b) che si possono
fare senza l'Anagrafe tributaria, cosi' lo scarto si scopre prima dell'invio.
"""

from dataclasses import dataclass, field
from datetime import datetime

from lxml import etree

from app.clock import ROMA
from app.domain.regole import AVVIO_DELEGA_UNICA, ModalitaFirma
from app.models import Delega
from app.services.cades import EsitoVerifica, Firmatario


@dataclass
class Controllo:
    errori: list[str] = field(default_factory=list)
    avvisi: list[str] = field(default_factory=list)


def _canonico(xml: bytes) -> bytes:
    parser = etree.XMLParser(remove_blank_text=True, resolve_entities=False, no_network=True)
    return etree.tostring(etree.fromstring(xml, parser), method="c14n")


def _controlla_firmatario(f: Firmatario, ruolo: str, adesso: datetime, esito: Controllo) -> None:
    if not f.valido_dal <= adesso <= f.valido_al:
        esito.errori.append(f"Il certificato {ruolo} non e' valido oggi")
    if f.ora_firma is None:
        esito.errori.append(f"Manca l'ora di firma {ruolo}")
        return
    giorno = f.ora_firma.astimezone(ROMA).date()
    if giorno < AVVIO_DELEGA_UNICA:
        esito.errori.append(f"La firma {ruolo} e' anteriore all'8 dicembre 2025")
    if f.ora_firma > adesso:
        esito.errori.append(f"La firma {ruolo} ha una data futura")


def controlla(delega: Delega, verifica: EsitoVerifica, adesso: datetime) -> Controllo:
    esito = Controllo()
    if delega.xml_generato is None:
        esito.errori.append("Scaricare prima il file XML della delega")
        return esito
    try:
        uguale = _canonico(verifica.contenuto) == _canonico(delega.xml_generato)
    except etree.XMLSyntaxError:
        uguale = False
    if not uguale:
        esito.errori.append("Il file firmato non contiene l'XML generato per questa delega")

    livelli = verifica.livelli
    doppia = delega.modalita_firma == ModalitaFirma.FEA_NON_QUALIFICATA
    if len(livelli) != (2 if doppia else 1):
        esito.errori.append(
            "Con la FEA non qualificata serve la busta del cliente firmata anche dallo studio"
            if doppia
            else "Il file deve contenere una sola busta firmata dal cliente"
        )
        return esito
    if any(len(liv.firmatari) != 1 for liv in livelli):
        esito.errori.append("Ogni busta deve avere un solo firmatario")
        return esito

    if delega.busta_cliente is not None and livelli[-1].busta != delega.busta_cliente:
        esito.errori.append("La busta del cliente non e' quella firmata con la piattaforma")
    cliente = livelli[-1].firmatari[0]
    _controlla_firmatario(cliente, "del cliente", adesso, esito)
    if cliente.codice_fiscale is None:
        msg = "Nel certificato del cliente non c'e' il codice fiscale (SERIALNUMBER TINIT-...)"
        (esito.errori if doppia else esito.avvisi).append(msg)
    elif cliente.codice_fiscale != delega.sottoscrittore_cf:
        esito.errori.append("Il certificato del cliente e' intestato a un altro codice fiscale")
    if cliente.ora_firma and cliente.ora_firma.astimezone(ROMA).date() < delega.data_firma:
        esito.errori.append("La data indicata nella delega e' successiva alla firma del cliente")
    if delega.modalita_firma == ModalitaFirma.FIRMA_DIGITALE and not cliente.qualificato:
        esito.errori.append("Il certificato del cliente non e' qualificato: non e' firma digitale")

    if doppia:
        studio = livelli[0].firmatari[0]
        _controlla_firmatario(studio, "dello studio", adesso, esito)
        if not studio.qualificato:
            esito.errori.append("La firma esterna dello studio deve essere una firma digitale")
        if studio.ora_firma and cliente.ora_firma and studio.ora_firma < cliente.ora_firma:
            esito.errori.append("La firma dello studio precede quella del cliente")
    return esito


def riepilogo(verifica: EsitoVerifica) -> list[dict]:
    return [
        {
            "livello": i,
            "firmatari": [
                {
                    "codice_fiscale": f.codice_fiscale,
                    "nome": f.nome_comune,
                    "emittente": f.emittente,
                    "qualificato": f.qualificato,
                    "ora_firma": f.ora_firma.isoformat() if f.ora_firma else None,
                }
                for f in liv.firmatari
            ],
        }
        for i, liv in enumerate(verifica.livelli, start=1)
    ]
