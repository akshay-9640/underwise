# Runs the web app with an OpenAI-compatible model endpoint (for example Ollama on the host:
# UNDERWISE_LLM_BASE_URL=http://host.docker.internal:11434/v1). The Claude Code CLI is not included.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY data ./data
RUN uv sync --frozen --no-dev
ENV UNDERWISE_DATA_DIR=/data/runs
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "underwise", "serve", "--host", "0.0.0.0", "--port", "8000"]
