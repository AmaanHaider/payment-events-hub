FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md
RUN pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir -e .

COPY src /app/src
COPY sample_data/sample_events.json /app/sample_data/sample_events.json

EXPOSE 8000
