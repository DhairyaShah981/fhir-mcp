FROM python:3.12-slim

# System deps kept minimal — fhir-mcp is a pure-Python server.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

# Install uv (fast, hermetic Python toolchain we already use in CI).
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Layer caching: copy lock + manifest first.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY evals ./evals

RUN uv sync --frozen --no-dev

# fhir-mcp listens on this port for SSE / streamable HTTP.
ENV FHIR_MCP_HOST=0.0.0.0
ENV FHIR_MCP_PORT=8000

# State + audit DB live here. Fly.io's volume is mounted at /data.
ENV FHIR_MCP_STATE_DIR=/data
RUN mkdir -p /data

EXPOSE 8000

# Default to Synthea backend so the demo works out-of-the-box with no creds.
ENV FHIR_MCP_BACKEND=synthea

# Bind to 0.0.0.0 so the container is reachable from Fly's proxy.
CMD ["uv", "run", "fhir-mcp", "serve", "--transport", "sse", "--host", "0.0.0.0", "--port", "8000"]
