FROM python:3.13-slim

RUN pip install --no-cache-dir poetry

WORKDIR /app
COPY pyproject.toml poetry.lock* ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --only main

COPY main.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
