# Delega Unica AdE

SaaS per intermediari (studi professionali, CAF) che gestisce la delega unica ai servizi online
dell'Agenzia delle Entrate e dell'Agenzia delle Entrate-Riscossione: raccolta e firma delle deleghe,
generazione del file XML, registro cronologico e conservazione.

Riferimenti: provvedimento AdE n. 375356 del 2 ottobre 2024 (modello e obblighi) e provvedimento
n. 321918 del 7 agosto 2025 (specifiche tecniche del file XML).

## Stato

Fase 3, FEA allo sportello dello studio (DPCM 22/2/2013, artt. 55-57). Per le persone fisiche
senza partita IVA che non hanno SPID, CIE o CNS:

1. Adesione: l'operatore identifica il cliente di persona, registra il documento esibito con la
   sua copia e il cellulare (`POST /clienti/{id}/adesioni-fea`). La piattaforma genera la
   dichiarazione di accettazione e il cliente la conferma con l'OTP ricevuto via SMS
   (`POST /adesioni-fea/{id}/conferma`).
2. Firma: con l'XML della delega davanti, il cliente riceve un OTP (`POST /deleghe/{id}/fea/otp`)
   legato all'impronta di quell'XML e lo inserisce (`POST /deleghe/{id}/fea/firma`). La piattaforma
   emette un certificato monouso con SERIALNUMBER=TINIT-codice fiscale, firma l'XML in CAdES e
   scarta la chiave.
3. Controfirma: lo studio scarica la busta del cliente (`GET /deleghe/{id}/fea/busta-cliente`), la
   firma con la propria firma digitale e la carica con `POST /deleghe/{id}/firmato`. Il caricamento
   accetta solo la busta prodotta dalla piattaforma.
4. Evidenze: copia del documento, dichiarazione, sessioni OTP, certificato, IP e operatore. Il
   database rifiuta modifiche e cancellazioni (le adesioni si possono solo revocare).

Configurazione: l'autorita' interna si crea con `uv run python -m app.cli crea-ca-fea`. In
sviluppo la chiave e' un file cifrato (`FEA_CA_CERT_PATH`, `FEA_CA_KEY_PATH`); in produzione sta
in AWS KMS (`--kms-key-id`, poi `FEA_CA_KMS_KEY_ID` e `FEA_CA_CERT_PEM`). L'invio degli SMS si
sceglie con `SMS_PROVIDER`: `console` scrive il codice nel log, `sns` usa AWS SNS. Il testo delle
condizioni del servizio (`app/services/fea.py`) va validato da un legale prima dell'uso.

## Documenti d'identita' dei clienti

Per le persone fisiche lo studio archivia la copia della carta d'identita' o del passaporto e la
piattaforma ne legge i dati per compilare l'anagrafica (`app/routers/documenti_identita.py`,
pagine nella scheda del cliente e in "Nuovo cliente").

- Lettura tutta in locale con Tesseract (`app/services/lettura_documenti.py`): dalla zona a
  lettura ottica (MRZ) del retro della CIE o del passaporto si ricavano cognome, nome, data di
  nascita, sesso, numero e scadenza. Numero e date si accettano solo se tornano le cifre di
  controllo ICAO; sul testo stampato si cerca anche il codice fiscale (carattere di controllo
  verificato). I dati letti si confrontano col codice fiscale del cliente e restano una proposta:
  l'operatore li conferma sul documento originale. Le vecchie carte cartacee e la patente non hanno
  MRZ: si archiviano e si compilano a mano. `POST /documenti-identita/lettura` legge senza salvare.
- Le copie sono cifrate nell'applicazione con AES-256-GCM, una chiave dati per copia legata a
  studio e documento (`app/services/cifratura.py`). In produzione la chiave principale e' in AWS
  KMS (`DOCUMENTI_KMS_KEY_ID`); altrove si usa `DOCUMENTI_CHIAVE` (32 byte in base64) o, in
  sviluppo, una chiave derivata da `SECRET_KEY`. Passando a KMS le copie gia' salvate restano
  leggibili finche' la chiave locale resta configurata. Nel database l'esito della lettura si
  conserva senza dati personali; ogni apertura, caricamento e cancellazione va nell'audit log.
- Conservazione: le copie dell'archivio si cancellano dalla scheda o con
  `uv run python -m app.cli elimina-documenti-scaduti --mesi 12`. All'adesione FEA si puo'
  riusare la copia archiviata: diventa evidenza dell'adesione e da li' si conserva vent'anni,
  indipendentemente dall'archivio.

## Produzione

Infrastruttura Terraform per AWS Milano e guida al primo deploy in
[infra/aws/README.md](infra/aws/README.md).

Fase 2, firme:

- Caricamento del file `.p7m` firmato (`POST /deleghe/{id}/firmato`) con verifica CAdES:
  integrita' del contenuto e della firma, certificato valido, codice fiscale del firmatario uguale
  a quello del sottoscrittore, date di firma ammesse, contenuto identico all'XML generato
- Firma digitale: il certificato deve essere qualificato (QcCompliance)
- FEA con certificato non qualificato: busta annidata con la firma del cliente all'interno e la
  firma qualificata dello studio all'esterno, apposta dopo quella del cliente
- La delega diventa "firmata" solo dopo la verifica, e solo allora entra nel registro;
  i rifiuti finiscono nell'audit log
- Motore di firma CAdES-B (`app/services/cades.py`) e CA interna per i certificati FEA
  (`app/services/ca_fea.py`), base per la FEA erogata dalla piattaforma
- Fornitura massiva (`POST /forniture`, fino a 300 deleghe) generata dai file firmati

Fase 1, registro e file XML:

- Anagrafica clienti (deleganti) con controllo formale del codice fiscale
- Deleghe: conferimento, revoca e rinnovo, con le regole dell'Allegato 2 (date di firma, finestra di
  rinnovo di 90 giorni, firme ammesse per titolari di P.IVA e societa', qualifica del sottoscrittore)
- Stati: bozza, in firma, firmata, inviata, attiva/accettata/scartata, sostituita, revocata
- Scadenza al 31 dicembre del quarto anno successivo; elenco delle deleghe in scadenza
- File XML DEL24 della singola delega e della fornitura massiva, validati sugli XSD ufficiali
  in `app/specifiche`
- Registro cronologico per studio: numerazione progressiva, catena di hash, sola aggiunta,
  verifica di integrita' ed export CSV

Fase 0, fondamenta:

- FastAPI + SQLAlchemy 2 + Alembic su PostgreSQL
- Multi-tenant: ogni studio e' un tenant, ogni riga applicativa porta `tenant_id`
- Ruoli: `admin`, `responsabile_deleghe` (figura prevista dal provvedimento), `operatore`
- Autenticazione con password (Argon2) e secondo fattore TOTP obbligatorio per usare le API
- Audit log di accessi e operazioni, in sola aggiunta (un trigger PostgreSQL rifiuta UPDATE e DELETE)

## Interfaccia web dello studio

Le pagine per gli operatori sono servite dalla stessa applicazione su `/studio` (codice in
`app/web`): template Jinja2 resi dal server e [HTMX](https://htmx.org) per le parti dinamiche,
senza un frontend separato. Ogni pagina richiama le stesse funzioni delle API, quindi regole,
controlli e audit log restano in un solo posto.

- Accesso con password e codice TOTP; al primo accesso la pagina mostra il QR da inquadrare
- Sessione in un cookie `HttpOnly`, `Secure`, `SameSite=Strict` che scade dopo
  `ACCESS_TOKEN_MINUTES` di inattivita'; token CSRF su ogni modulo; Content Security Policy
  senza script in linea
- Cruscotto con le deleghe da completare e quelle in scadenza
- Clienti: ricerca, scheda, anagrafica, adesione alla FEA con identificazione di persona
- Deleghe: conferimento, revoca e rinnovo, file XML, caricamento del `.p7m`, invio ed esito,
  fornitura massiva
- FEA allo sportello: codice via SMS al cliente, firma, busta da controfirmare
- Registro cronologico con filtro per date, verifica della catena di hash ed export CSV
  (amministratore e responsabile delle deleghe)
- Studio: dati dell'intermediario e utenti

HTMX 2.0.4 e' incluso in `app/web/static` (nessuna risorsa da CDN esterni).

## Sviluppo locale

Requisiti: Python 3.12+, [uv](https://docs.astral.sh/uv/), PostgreSQL 16 (o Docker).

```bash
docker compose up -d db
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run python -m app.cli create-tenant \
    --name "Studio Rossi" --codice-fiscale 01234567890 \
    --admin-email admin@studiorossi.it --admin-name "Mario Rossi"
uv run uvicorn app.main:app --reload
```

Interfaccia web su http://localhost:8000/studio, documentazione delle API su
http://localhost:8000/docs.

### Flusso di accesso

1. `POST /auth/login` con email e password.
2. Se il TOTP non e' ancora configurato, la risposta ha `mfa_setup_required: true`: con il token
   ricevuto chiamare `POST /auth/totp/setup`, inquadrare `otpauth_uri` con un'app di autenticazione
   e confermare con `POST /auth/totp/confirm`.
3. Dai login successivi la risposta ha `mfa_required: true`: inviare il codice a `POST /auth/login/mfa`.

### Test

I test girano su un database PostgreSQL dedicato (`TEST_DATABASE_URL`, predefinito
`postgresql+psycopg://delega:delega@localhost:5432/delega_test`).

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest
```
