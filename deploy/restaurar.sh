#!/usr/bin/env bash
# AVZCloud Central Manager - restaura un respaldo (.dump de respaldo.sh) en una base NUEVA y VACIA del stack
# y muestra conteos de filas por tabla frente a la base activa. NO toca ni reemplaza la base activa: la
# promocion (renombrar) es un paso manual explicito que se imprime al final.

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
ENV_FILE="$ROOT/.env"

PROYECTO_FLAG=""; ARCHIVO=""; CONFIRMA=0; DESTINO_DB=""

uso() {
  cat <<'USO'
Uso: deploy/restaurar.sh ARCHIVO.dump --si-estoy-seguro [--proyecto NOMBRE] [--base-destino NOMBRE] [--ayuda]

  ARCHIVO.dump          Volcado generado por deploy/respaldo.sh (formato custom de pg_dump).
  --si-estoy-seguro     Confirmacion explicita. Sin ella el script solo verifica el archivo y termina.
  --proyecto NOMBRE     Proyecto Compose (por defecto el de COMPOSE_PROJECT_NAME en .env, o "mikrotik-manager").
  --base-destino NOMBRE Base nueva donde restaurar (por defecto <POSTGRES_DB>_restaurada). Debe NO existir.
  --ayuda               Muestra esta ayuda.

Que hace: crea la base de destino, restaura el volcado en ella y compara COUNT(*) por tabla con la base activa.
No detiene la API ni modifica la base activa.
USO
}

while [ $# -gt 0 ]; do
  case "$1" in
    --proyecto) PROYECTO_FLAG="${2:-}"; shift 2 ;;
    --base-destino) DESTINO_DB="${2:-}"; shift 2 ;;
    --si-estoy-seguro) CONFIRMA=1; shift ;;
    --ayuda|-h|--help) uso; exit 0 ;;
    -*) echo "Opcion desconocida: $1 (use --ayuda)" >&2; exit 2 ;;
    *) [ -z "$ARCHIVO" ] && ARCHIVO="$1" || { echo "Solo se admite un archivo" >&2; exit 2; }; shift ;;
  esac
done

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

envget() {
  [ -f "$1" ] || return 0
  grep -E "^$2=" "$1" | tail -n 1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

[ -n "$ARCHIVO" ] || { uso; exit 2; }
[ -f "$ARCHIVO" ] && [ -s "$ARCHIVO" ] || die "no existe o esta vacio: $ARCHIVO"
[ -f "$ENV_FILE" ] || die "no existe $ENV_FILE"
PROYECTO="${PROYECTO_FLAG:-$(envget "$ENV_FILE" COMPOSE_PROJECT_NAME)}"; PROYECTO="${PROYECTO:-mikrotik-manager}"
[[ "$PROYECTO" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "nombre de proyecto invalido: $PROYECTO"
DB_ACTIVA="$(envget "$ENV_FILE" POSTGRES_DB)"
[ -n "$DB_ACTIVA" ] || die "POSTGRES_DB vacio en .env"
DESTINO_DB="${DESTINO_DB:-${DB_ACTIVA}_restaurada}"
[[ "$DESTINO_DB" =~ ^[a-z_][a-z0-9_]*$ ]] || die "nombre de base de destino invalido: $DESTINO_DB (minusculas, digitos y _)"
[ "$DESTINO_DB" != "$DB_ACTIVA" ] || die "la base de destino no puede ser la activa ($DB_ACTIVA)"

command -v docker >/dev/null 2>&1 || die "Docker no esta instalado"
docker info >/dev/null 2>&1 || die "este usuario no puede usar Docker"

dc() { docker compose -p "$PROYECTO" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
psql_db() { dc exec -T mikrotik-db sh -c 'psql -U "$POSTGRES_USER" -d "$0" -At -v ON_ERROR_STOP=1' "$1"; }   # SQL por stdin

CID="$(dc ps -q mikrotik-db 2>/dev/null | head -n 1)"
[ -n "$CID" ] || die "no hay contenedor de base de datos del proyecto '$PROYECTO'. Levante la base: docker compose -p $PROYECTO --env-file $ENV_FILE -f $COMPOSE_FILE up -d mikrotik-db"
ESTADO="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CID" 2>/dev/null)"
[ "$ESTADO" = "healthy" ] || die "la base no esta sana (estado: $ESTADO)"

echo "Verificando el volcado ($ARCHIVO)..."
dc exec -T mikrotik-db pg_restore --list < "$ARCHIVO" > /dev/null 2>&1 || die "el archivo no es un volcado valido de pg_dump -Fc"
N_TABLAS="$(dc exec -T mikrotik-db pg_restore --list < "$ARCHIVO" 2>/dev/null | grep -c 'TABLE DATA' || true)"
echo "  legible, con $N_TABLAS tabla(s) con datos."

if [ "$CONFIRMA" != 1 ]; then
  echo
  echo "Sin --si-estoy-seguro no se restaura nada. Se crearia la base '$DESTINO_DB' en el proyecto '$PROYECTO' y se"
  echo "restauraria alli el volcado (la base activa '$DB_ACTIVA' no se toca). Repita el comando agregando --si-estoy-seguro."
  exit 1
fi

EXISTE="$(echo "SELECT 1 FROM pg_database WHERE datname = '$DESTINO_DB'" | psql_db postgres | tr -d '[:space:]')"
[ -z "$EXISTE" ] || die "la base '$DESTINO_DB' ya existe. Elija otro --base-destino o eliminela a mano (DROP DATABASE $DESTINO_DB) tras revisarla."

echo "Creando la base vacia '$DESTINO_DB'..."
echo "CREATE DATABASE \"$DESTINO_DB\"" | psql_db postgres >/dev/null || die "no se pudo crear la base de destino"

echo "Restaurando (puede tardar)..."
if ! dc exec -T mikrotik-db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$0" --no-owner --exit-on-error' "$DESTINO_DB" < "$ARCHIVO"; then
  echo "La restauracion fallo. La base parcial '$DESTINO_DB' se conserva para diagnostico; elimine con:" >&2
  echo "  echo 'DROP DATABASE $DESTINO_DB' | docker compose -p $PROYECTO --env-file $ENV_FILE -f $COMPOSE_FILE exec -T mikrotik-db sh -c 'psql -U \"\$POSTGRES_USER\" -d postgres'" >&2
  exit 1
fi
echo "  restauracion terminada."

SQL_CONTEO="SELECT relname || '|' || (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', schemaname, relname), false, true, '')))[1]::text FROM pg_stat_user_tables ORDER BY relname"
TMPD="$(mktemp -d)"; trap 'rm -rf "$TMPD"' EXIT
echo "$SQL_CONTEO" | psql_db "$DB_ACTIVA"  | LC_ALL=C sort > "$TMPD/activa.txt"  || die "no se pudieron contar las filas de la base activa"
echo "$SQL_CONTEO" | psql_db "$DESTINO_DB" | LC_ALL=C sort > "$TMPD/restaurada.txt" || die "no se pudieron contar las filas de la base restaurada"

echo
echo "Conteo de filas por tabla (COUNT(*) exacto):"
printf '  %-34s %12s %12s  %s\n' "TABLA" "ACTIVA" "RESTAURADA" "ESTADO"
DIFERENCIAS=0
while IFS='|' read -r tabla a r; do
  est="igual"; [ "$a" = "$r" ] || { est="DIFERENTE"; DIFERENCIAS=$((DIFERENCIAS + 1)); }
  printf '  %-34s %12s %12s  %s\n' "$tabla" "$a" "$r" "$est"
done < <(LC_ALL=C join -t '|' -a1 -a2 -e '(falta)' -o 0,1.2,2.2 "$TMPD/activa.txt" "$TMPD/restaurada.txt")
echo
if [ "$DIFERENCIAS" -eq 0 ]; then
  echo "Todas las tablas coinciden con la base activa."
else
  echo "$DIFERENCIAS tabla(s) difieren. Es normal si la base activa cambio despues del respaldo (o si restaura un respaldo antiguo)."
fi

cat <<FIN

La base activa ('$DB_ACTIVA') NO se modifico. Si necesita REEMPLAZARLA por la restaurada (accion destructiva,
hagala con respaldo reciente y en ventana de mantenimiento):
  1. docker compose -p $PROYECTO --env-file $ENV_FILE -f $COMPOSE_FILE stop mikrotik-api
  2. En la base 'postgres' del contenedor:
       ALTER DATABASE $DB_ACTIVA RENAME TO ${DB_ACTIVA}_anterior;
       ALTER DATABASE $DESTINO_DB RENAME TO $DB_ACTIVA;
  3. docker compose -p $PROYECTO --env-file $ENV_FILE -f $COMPOSE_FILE start mikrotik-api
  El .env debe ser el del respaldo (misma VAULT_KEY) para que la API pueda descifrar las claves guardadas.
FIN
