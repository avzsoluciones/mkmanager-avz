#!/usr/bin/env bash
# AVZCloud Central Manager - respaldo de la base de datos y del .env.
# Genera (permisos 600, fecha en el nombre):
#   mkmanager-<proyecto>-<fecha>.dump    pg_dump -Fc desde el contenedor de la base (verificado con pg_restore --list)
#   mkmanager-<proyecto>-<fecha>.env     copia del .env (contiene SECRET_KEY y VAULT_KEY: guardarla en el gestor de secretos)
#   mkmanager-<proyecto>-<fecha>.sha256  suma de verificacion de ambos
# Sin VAULT_KEY el volcado no sirve para recuperar las claves WireGuard cifradas: se respaldan JUNTOS pero se
# guardan en sitios separados (el .env en el gestor de secretos, el .dump en el almacen de respaldos).

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
ENV_FILE="$ROOT/.env"

PROYECTO_FLAG=""; DESTINO=""

uso() {
  cat <<'USO'
Uso: deploy/respaldo.sh [--proyecto NOMBRE] [--destino DIRECTORIO] [--ayuda]

  --proyecto NOMBRE     Proyecto Compose (por defecto el de COMPOSE_PROJECT_NAME en .env, o "mikrotik-manager").
  --destino DIRECTORIO  Donde guardar los archivos (por defecto $HOME/respaldos-mkmanager/<proyecto>, modo 700).
                        Elegir un directorio FUERA del repositorio.
  --ayuda               Muestra esta ayuda.

Requisitos: el stack levantado (la base en estado healthy). No interrumpe el servicio.
Programar con cron, p. ej. diario a las 03:15:
  15 3 * * * /ruta/al/repo/deploy/respaldo.sh --proyecto mikrotik-manager >> /var/log/mkmanager-respaldo.log 2>&1
USO
}

while [ $# -gt 0 ]; do
  case "$1" in
    --proyecto) PROYECTO_FLAG="${2:-}"; shift 2 ;;
    --destino) DESTINO="${2:-}"; shift 2 ;;
    --ayuda|-h|--help) uso; exit 0 ;;
    *) echo "Opcion desconocida: $1 (use --ayuda)" >&2; exit 2 ;;
  esac
done

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

envget() {
  [ -f "$1" ] || return 0
  grep -E "^$2=" "$1" | tail -n 1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

[ -f "$ENV_FILE" ] || die "no existe $ENV_FILE (este script se ejecuta en el servidor donde se instalo el stack)"
PROYECTO="${PROYECTO_FLAG:-$(envget "$ENV_FILE" COMPOSE_PROJECT_NAME)}"; PROYECTO="${PROYECTO:-mikrotik-manager}"
[[ "$PROYECTO" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "nombre de proyecto invalido: $PROYECTO"
DESTINO="${DESTINO:-${HOME:-/root}/respaldos-mkmanager/$PROYECTO}"

command -v docker >/dev/null 2>&1 || die "Docker no esta instalado"
docker info >/dev/null 2>&1 || die "este usuario no puede usar Docker"

dc() { docker compose -p "$PROYECTO" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }

CID="$(dc ps -q mikrotik-db 2>/dev/null | head -n 1)"
[ -n "$CID" ] || die "no hay contenedor de base de datos del proyecto '$PROYECTO' (esta levantado el stack?)"
ESTADO="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CID" 2>/dev/null)"
[ "$ESTADO" = "healthy" ] || die "la base no esta sana (estado: $ESTADO)"

umask 077
mkdir -p "$DESTINO" || die "no se pudo crear $DESTINO"
chmod 700 "$DESTINO"

SELLO="$(date +%Y%m%d-%H%M%S)"
BASE="$DESTINO/mkmanager-$PROYECTO-$SELLO"
TMP="$BASE.dump.parcial"
trap 'rm -f "$TMP"' EXIT

echo "Respaldando la base del proyecto '$PROYECTO'..."
dc exec -T mikrotik-db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$TMP" || die "pg_dump fallo"
[ -s "$TMP" ] || die "el volcado esta vacio"

# Verifica que el volcado sea legible y que contenga tablas.
TABLAS="$(dc exec -T mikrotik-db pg_restore --list < "$TMP" 2>/dev/null | grep -c 'TABLE DATA' || true)"
if ! dc exec -T mikrotik-db pg_restore --list < "$TMP" >/dev/null 2>&1; then
  die "el volcado NO es legible por pg_restore --list; se descarta"
fi
[ "${TABLAS:-0}" -gt 0 ] || die "el volcado no contiene datos de tablas; se descarta"

mv "$TMP" "$BASE.dump" && chmod 600 "$BASE.dump"
cp "$ENV_FILE" "$BASE.env" && chmod 600 "$BASE.env"
( cd "$DESTINO" && sha256sum "$(basename "$BASE").dump" "$(basename "$BASE").env" > "$(basename "$BASE").sha256" && chmod 600 "$(basename "$BASE").sha256" )

TAM="$(du -h "$BASE.dump" | cut -f1)"
echo
echo "OK  volcado ...... $BASE.dump ($TAM, $TABLAS tablas con datos, verificado con pg_restore --list)"
echo "OK  copia de .env  $BASE.env"
echo "OK  sumas ........ $BASE.sha256"
echo
echo "IMPORTANTE:"
echo " - El .env contiene SECRET_KEY y VAULT_KEY: guarde esa copia en el gestor de secretos de AVZ (no junto al volcado)."
echo "   Sin VAULT_KEY no se pueden recuperar las claves WireGuard cifradas en la base."
echo " - Copie el .dump FUERA de este servidor (otra maquina o almacenamiento de objetos)."
echo " - Pruebe periodicamente la restauracion con deploy/restaurar.sh (en una base aparte, no toca la activa)."
