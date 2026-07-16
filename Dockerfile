FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin appuser

COPY requirements ./requirements
ARG REQUIREMENTS_FILE=requirements/production.txt
RUN pip install --upgrade pip && pip install -r "${REQUIREMENTS_FILE}"

COPY --chown=appuser:appuser . .
RUN python manage.py collectstatic --noinput

USER appuser

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-"]
