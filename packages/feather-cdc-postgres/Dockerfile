# Python 3.12 — the fleet-common version (#73). Build context is this dir (Railway Root
# Directory = pg_md_cdc) so the image stays self-contained (ADR 0023). ONE image serves every
# registration; the service instance picks its manifest via CDC_REGISTRATION (#3079 D12).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev
COPY src ./src
COPY registrations ./registrations
RUN uv sync --locked --no-dev
ENV PATH="/app/.venv/bin:${PATH}"
# Cron service: drain one registration's slot into its bronze database and EXIT.
# Secrets arrive as env vars named by the manifest (dsn_env / token_env / CONTROL_PG_DSN).
CMD ["sh", "-c", "python -m pg_md_cdc run registrations/${CDC_REGISTRATION:?CDC_REGISTRATION not set}.yml"]
