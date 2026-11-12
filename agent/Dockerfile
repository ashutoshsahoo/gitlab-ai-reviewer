FROM python:3.13-slim

ARG INSTALL_SEMGREP=true
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
COPY requirements-semgrep.txt .
RUN pip install -r requirements.txt \
    && if [ "$INSTALL_SEMGREP" = "true" ]; then pip install -r requirements-semgrep.txt; fi

COPY app ./app
COPY rules ./rules
COPY samples ./samples

RUN useradd --create-home --uid 10001 reviewer && chown -R reviewer /app
USER reviewer

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
