# Interfaz estatica de AVZCloud Central Manager servida por nginx SIN privilegios (usuario 101, puerto 8080).
# Contexto de construccion: ./frontend-new  (js/mock.js se excluye por .dockerignore).

# --- Etapa 1: prepara los archivos y versiona los assets referenciados desde index.html -----------
FROM alpine:3.20 AS prep
ARG ASSET_VERSION=1
WORKDIR /site
COPY index.html ./
COPY css ./css
COPY js ./js
COPY assets ./assets
# Solo caracteres seguros en la version (va dentro de una URL y de un sed).
RUN printf '%s' "$ASSET_VERSION" | grep -Eq '^[A-Za-z0-9._-]{1,32}$' \
 && sed -i -E "s#(href|src)=\"((css|js)/[^\"?]+)\"#\1=\"\2?v=${ASSET_VERSION}\"#g" index.html \
 && grep -q "?v=${ASSET_VERSION}" index.html

# --- Etapa 2: nginx sin privilegios ------------------------------------------------------------------
# Pendiente (SEG-025): fijar la imagen por digest.
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/nginx.conf
COPY security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=prep /site /usr/share/nginx/html

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
# La imagen base ya ejecuta como usuario 101 (nginx). nginx.conf escribe solo en /tmp.
CMD ["nginx", "-g", "daemon off;"]
