FROM python:3.12-slim AS base

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir -e .

FROM base AS textstrata-lite
ENTRYPOINT ["python3", "-m", "textstrata"]
CMD ["--help"]

FROM base AS textstrata
ARG TEXTSTRATA_EXTRAS=""
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
RUN if [ -n "$TEXTSTRATA_EXTRAS" ]; then pip install --no-cache-dir -e ".[${TEXTSTRATA_EXTRAS}]"; fi
ENTRYPOINT ["python3", "-m", "textstrata"]
CMD ["--help"]

FROM base AS textstrata-mcp
ENTRYPOINT ["python3", "-m", "textstrata.mcp_server"]
