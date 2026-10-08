FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --upgrade pip && pip install . && chown -R app:app /app

USER app

EXPOSE 8000

# Workers and timeout are overridable without rebuilding the image, because the right number
# depends on the host's CPU count and on the PostgreSQL connection budget rather than on the
# code. See DEPLOYMENT.md for how to choose them.
CMD ["sh", "-c", "alembic upgrade head && exec gunicorn app.main:app --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-2} --timeout ${GUNICORN_TIMEOUT:-120} --graceful-timeout 30 --worker-class uvicorn.workers.UvicornWorker --access-logfile - --error-logfile -"]
