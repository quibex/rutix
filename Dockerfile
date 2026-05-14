FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

FROM python:3.12-slim
WORKDIR /app
RUN mkdir -p /app/data
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src
CMD ["sh", "-c", "alembic upgrade head && python -m rutix"]
