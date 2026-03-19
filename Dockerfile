FROM python:3.13-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# uncomment the following line should you have any troubles installing certain packages which require C/C++ extensions
# to be compiled during installation, eg. numpy, psycopg2, …
# RUN apt-get update && apt-get install -y build-essential

WORKDIR /code/
COPY pyproject.toml uv.lock ./

ENV UV_PROJECT_ENVIRONMENT="/usr/local/"
RUN uv sync --no-dev --frozen

COPY src/ src/
COPY scripts/ scripts/
COPY deploy.sh .

FROM base AS test
RUN uv sync --all-groups --frozen
COPY tests/ tests/
RUN uv run ruff check src/ tests/
CMD ["uv", "run", "pytest", "tests/", "-v"]

FROM base AS production
CMD ["python", "-u", "/code/src/component.py"]
