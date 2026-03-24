FROM python:3.12-alpine

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ src/

RUN uv pip install --system --no-cache .

EXPOSE 4000

ENTRYPOINT ["ai-replay"]
CMD ["--help"]
