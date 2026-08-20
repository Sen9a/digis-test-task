FROM python:3.13-slim

RUN pip install --no-cache-dir poetry

WORKDIR /app
COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --only main --no-root

COPY src/ src/
COPY main.py settings.py exceptions.py .

CMD ["python", "-m", "main"]
