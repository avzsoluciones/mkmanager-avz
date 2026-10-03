# Imagen de la API de AVZCloud Central Manager (endurecida: SEG-016).
# Pendiente (SEG-025): fijar la imagen por digest y las dependencias por version/hash.
FROM python:3.11-slim

# Sin .pyc en tiempo de ejecucion (el sistema de archivos del contenedor es de solo lectura),
# salida sin buffer para los logs y pip sin cache ni aviso de version.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Usuario del sistema sin shell ni home: el proceso no corre como root.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Solo lo necesario para ejecutar. Los archivos son de root y de solo lectura para `app`:
# un fallo de la API no puede modificar su propio codigo ni las migraciones.
# (tests/, .venv/, .env y caches quedan fuera por .dockerignore.)
COPY --chown=root:root app ./app
COPY --chown=root:root migrations ./migrations

USER 10001
EXPOSE 8000

# Sin curl: se usa Python. Falla si /api/v1/health responde distinto de 200 (503 = BD caida).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"]

# --proxy-headers: la IP real viene de X-Forwarded-For SOLO si el par esta en FORWARDED_ALLOW_IPS
# (variable de entorno que lee uvicorn; el compose la fija). --no-server-header oculta la version.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
