# Delega Unica AdE

SaaS per intermediari (studi professionali, CAF) che gestisce la delega unica ai servizi online
dell'Agenzia delle Entrate e dell'Agenzia delle Entrate-Riscossione: raccolta e firma delle deleghe,
generazione del file XML, registro cronologico e conservazione.

Riferimenti: provvedimento AdE n. 375356 del 2 ottobre 2024 (modello e obblighi) e provvedimento
n. 321918 del 7 agosto 2025 (specifiche tecniche del file XML).

## Stato

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

Documentazione delle API su http://localhost:8000/docs.

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
