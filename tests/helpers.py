from app.domain.codice_fiscale import carattere_controllo_pf, cifra_controllo_pnf


def cf_pf(primi15: str) -> str:
    return primi15 + carattere_controllo_pf(primi15)


def cf_pnf(prime10: str) -> str:
    return prime10 + cifra_controllo_pnf(prime10)


MARIO = cf_pf("RSSMRA80A01H501")
LAURA = cf_pf("BNCLRA85M41F205")
STUDIO = cf_pnf("0123456789")
SOCIETA = cf_pnf("9876543210")
