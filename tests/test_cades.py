import base64
import shutil
import subprocess
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization

from app.services import cades
from tests.helpers import LAURA, MARIO


def test_firma_e_verifica(pki):
    busta = cades.firma(b"<delega/>", *pki["mario_fea"], catena=[pki["ca"].certificato])
    esito = cades.verifica(busta)
    assert esito.contenuto == b"<delega/>"
    firmatario = esito.livelli[0].firmatari[0]
    assert firmatario.codice_fiscale == MARIO
    assert firmatario.qualificato is False


def test_busta_annidata(pki):
    interna = cades.firma(b"<delega/>", *pki["mario_fea"])
    esterna = cades.firma(interna, *pki["studio"])
    esito = cades.verifica(esterna)
    assert [liv.firmatari[0].codice_fiscale for liv in esito.livelli] == [LAURA, MARIO]
    assert esito.livelli[0].firmatari[0].qualificato is True
    assert esito.contenuto == b"<delega/>"


def test_contenuto_alterato(pki):
    busta = bytearray(cades.firma(b"<delega>A</delega>", *pki["mario_fea"]))
    busta[busta.find(b">A<") + 1] = ord("B")
    with pytest.raises(cades.BustaNonValida, match="modificato"):
        cades.verifica(bytes(busta))


def test_base64_rifiutato(pki):
    busta = cades.firma(b"<delega/>", *pki["mario_fea"])
    with pytest.raises(cades.BustaNonValida, match="base64"):
        cades.verifica(base64.b64encode(busta))


def test_file_non_firmato():
    with pytest.raises(cades.BustaNonValida):
        cades.verifica(b"<?xml version='1.0'?><delega/>")


def test_ora_di_firma(pki):
    ora = datetime(2026, 10, 20, 9, 30, tzinfo=UTC)
    busta = cades.firma(b"x", *pki["mario_fea"], ora=ora)
    assert cades.verifica(busta).livelli[0].firmatari[0].ora_firma == ora


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl non disponibile")
def test_interoperabilita_openssl(pki, tmp_path):
    chiave, cert = pki["mario_fea"]
    (tmp_path / "ca.pem").write_bytes(
        pki["ca"].certificato.public_bytes(serialization.Encoding.PEM)
    )
    (tmp_path / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "key.pem").write_bytes(
        chiave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    # La nostra busta e' verificata da OpenSSL
    (tmp_path / "nostra.p7m").write_bytes(cades.firma(b"<delega/>", chiave, cert))
    subprocess.run(
        [
            "openssl",
            "cms",
            "-verify",
            "-inform",
            "DER",
            "-in",
            tmp_path / "nostra.p7m",
            "-CAfile",
            tmp_path / "ca.pem",
            "-purpose",
            "any",
            "-out",
            tmp_path / "out.xml",
        ],
        check=True,
        capture_output=True,
    )
    assert (tmp_path / "out.xml").read_bytes() == b"<delega/>"

    # Una busta CAdES prodotta da OpenSSL e' verificata da noi
    (tmp_path / "in.xml").write_bytes(b"<delega>openssl</delega>")
    subprocess.run(
        [
            "openssl",
            "cms",
            "-sign",
            "-binary",
            "-nodetach",
            "-cades",
            "-md",
            "sha256",
            "-in",
            tmp_path / "in.xml",
            "-signer",
            tmp_path / "cert.pem",
            "-inkey",
            tmp_path / "key.pem",
            "-outform",
            "DER",
            "-out",
            tmp_path / "loro.p7m",
        ],
        check=True,
        capture_output=True,
    )
    esito = cades.verifica((tmp_path / "loro.p7m").read_bytes())
    assert esito.contenuto == b"<delega>openssl</delega>"
    assert esito.livelli[0].firmatari[0].codice_fiscale == MARIO
