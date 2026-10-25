"""Adattatori AWS (KMS, SNS) provati con client finti che imitano le API di boto3."""

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from sqlalchemy import make_url

from app.config import Settings
from app.services import ca_fea, cades, sms
from tests.helpers import MARIO


class KmsFinto:
    """Chiave RSA_4096 SIGN_VERIFY: la privata resta qui dentro, come nel modulo hardware."""

    def __init__(self):
        self._privata = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        self.firme = 0

    def get_public_key(self, KeyId):
        der = self._privata.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return {"KeyId": KeyId, "PublicKey": der}

    def sign(self, KeyId, Message, MessageType, SigningAlgorithm):
        assert MessageType == "DIGEST" and len(Message) == 32
        assert SigningAlgorithm == "RSASSA_PKCS1_V1_5_SHA_256"
        self.firme += 1
        firma = self._privata.sign(Message, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))
        return {"KeyId": KeyId, "Signature": firma}


def test_autorita_con_chiave_kms():
    kms = KmsFinto()
    chiave = ca_fea.ChiaveKms(kms, "alias/delega-ca-fea")
    autorita = ca_fea.crea_autorita("Studio Test", chiave=chiave)
    pem = autorita.certificato.public_bytes(serialization.Encoding.PEM)

    # In produzione si riparte dal solo certificato PEM e dall'identificativo della chiave
    caricata = ca_fea.carica_autorita_kms(pem, kms, "alias/delega-ca-fea")
    privata, cert = ca_fea.emetti_certificato(caricata, MARIO, "Rossi", "Mario")
    caricata.certificato.public_key().verify(
        cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), hashes.SHA256()
    )
    assert cert.issuer == autorita.certificato.subject
    assert kms.firme == 2

    busta = cades.firma(b"<delega/>", privata, cert, [caricata.certificato])
    assert cades.verifica(busta).livelli[0].firmatari[0].codice_fiscale == MARIO


def test_certificato_kms_che_non_corrisponde():
    autorita = ca_fea.crea_autorita("Studio Test", chiave=ca_fea.ChiaveKms(KmsFinto(), "k1"))
    pem = autorita.certificato.public_bytes(serialization.Encoding.PEM)
    try:
        ca_fea.carica_autorita_kms(pem, KmsFinto(), "k2")
    except ValueError as exc:
        assert "non corrisponde" in str(exc)
    else:
        raise AssertionError("certificato accettato con una chiave diversa")


class SnsFinto:
    def __init__(self):
        self.pubblicati = []

    def publish(self, **kw):
        self.pubblicati.append(kw)
        return {"MessageId": "1"}


def test_sms_con_sns():
    sns = SnsFinto()
    sms.SmsAws(sns, "StudioRossi").invia("3331234567", "codice 123456")
    inviato = sns.pubblicati[0]
    assert inviato["PhoneNumber"] == "+393331234567"
    assert inviato["MessageAttributes"]["AWS.SNS.SMS.SMSType"]["StringValue"] == "Transactional"
    assert sms.e164("00393331234567") == "+393331234567"
    assert sms.e164("+41791234567") == "+41791234567"


def test_url_database_dai_parametri():
    s = Settings(db_host="db.example.eu", db_password="p@ss%/word", db_name="delega")
    url = make_url(s.database_url)
    assert url.password == "p@ss%/word"
    assert url.host == "db.example.eu"
    assert url.query["sslmode"] == "require"


def test_comando_crea_ca_fea_con_kms(monkeypatch):
    import boto3
    from cryptography import x509
    from typer.testing import CliRunner

    from app.cli import cli

    kms = KmsFinto()
    monkeypatch.setattr(boto3, "client", lambda servizio, region_name: kms)
    res = CliRunner().invoke(
        cli, ["crea-ca-fea", "--denominazione", "Studio Test", "--kms-key-id", "alias/ca"]
    )
    assert res.exit_code == 0, res.output
    cert = x509.load_pem_x509_certificate(res.output.encode())
    assert cert.subject == cert.issuer
    ca_fea.carica_autorita_kms(res.output.encode(), kms, "alias/ca")
