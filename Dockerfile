FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY worker_env/requirements.txt /app/worker_env/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/worker_env/requirements.txt \
    && playwright install --with-deps chromium

COPY . /app
RUN mkdir -p /app/worker_env/stored_data /app/worker_env/stored_data/artifacts /app/worker_env/stored_data/snapshots

EXPOSE 8000
CMD ["python", "-m", "worker_env.src.app"]
