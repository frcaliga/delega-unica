FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.4.30 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
RUN useradd --system --uid 10001 app
USER app
EXPOSE 8000
# Dietro il bilanciatore: l'IP del cliente arriva in X-Forwarded-For e finisce nelle evidenze FEA.
# Il container accetta connessioni solo dal bilanciatore (gruppo di sicurezza).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'"]
