# Contexto de build: la raiz del monorepo (el backend depende del SDK por path).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY packages/sdk-python /app/packages/sdk-python
COPY apps/backend /app/apps/backend

RUN pip install --no-cache-dir /app/packages/sdk-python \
 && pip install --no-cache-dir /app/apps/backend

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=10 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "laplace_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
