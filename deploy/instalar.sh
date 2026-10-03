#!/usr/bin/env bash
# AVZCloud Central Manager - instalacion LIMPIA (servidor nuevo). Idempotente: se puede re-ejecutar.
# Procedimiento completo: deploy/INSTALACION-NUEVO-SERVIDOR.md. Para migrar el srv-2 actual NO se usa este script
# (ver deploy/DEPLOY.md).
#
# Seguridad: nunca imprime secretos ni los pasa por argumentos (visibles en `ps`): los escribe en .env (600)
# con awk leyendolos del entorno, y la contrasena del superadmin viaja solo por variable de entorno al contenedor.

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
ENV_FILE="$ROOT/.env"
ENV_EXAMPLE="$ROOT/.env.example"

PROYECTO_FLAG=""; API_PORT_FLAG=""; WEB_PORT_FLAG=""; SUBRED_FLAG=""; DOMINIO=""
SIN_HUB=0; NO_SUPERADMIN=0; ADMIN_PW_FILE=""; SOLO_VERIFICAR=0

uso() {
  cat <<'USO'
Uso: deploy/instalar.sh [opciones]

Instala AVZCloud Central Manager en este servidor con Docker Compose (instalacion limpia).

Opciones:
  --proyecto NOMBRE           Nombre del proyecto Compose (prefijo de contenedores, red y volumen).
                              Por defecto: el de COMPOSE_PROJECT_NAME en .env, o "mikrotik-manager".
                              Usar otro nombre (y otros puertos/subred) permite un segundo stack de prueba.
  --api-port PUERTO           Puerto local de la API en el host (por defecto 8090, solo loopback).
  --web-port PUERTO           Puerto local de la interfaz web en el host (por defecto 8091, solo loopback).
  --subred CIDR               Subred de la red edge-net (por defecto 172.29.91.0/24). La pasarela sera la .1.
  --sin-hub                   No exige ruta hacia HUB_HOST (instalar sin enlace al hub, p. ej. para pruebas).
  --dominio NOMBRE            Dominio publico previsto (solo se usa para el resumen y el ejemplo de proxy).
  --no-superadmin             No crea/actualiza el superadmin.
  --admin-password-file RUTA  Lee la contrasena del superadmin de la primera linea de ese archivo (modo 600)
                              en lugar de pedirla. Con esta opcion se (re)establece aunque ya exista.
  --solo-verificar            Solo comprueba prerrequisitos y termina (no crea .env ni levanta nada).
  --ayuda                     Muestra esta ayuda.

Ejemplo:
  deploy/instalar.sh --dominio mk.ejemplo.com
  deploy/instalar.sh --proyecto mk-prueba --api-port 18090 --web-port 18091 --subred 172.29.92.0/24 --sin-hub
USO
}

while [ $# -gt 0 ]; do
  case "$1" in
    --proyecto) PROYECTO_FLAG="${2:-}"; shift 2 ;;
    --api-port) API_PORT_FLAG="${2:-}"; shift 2 ;;
    --web-port) WEB_PORT_FLAG="${2:-}"; shift 2 ;;
    --subred) SUBRED_FLAG="${2:-}"; shift 2 ;;
    --dominio) DOMINIO="${2:-}"; shift 2 ;;
    --sin-hub) SIN_HUB=1; shift ;;
    --no-superadmin) NO_SUPERADMIN=1; shift ;;
    --admin-password-file) ADMIN_PW_FILE="${2:-}"; shift 2 ;;
    --solo-verificar) SOLO_VERIFICAR=1; shift ;;
    --ayuda|-h|--help) uso; exit 0 ;;
    *) echo "Opcion desconocida: $1 (use --ayuda)" >&2; exit 2 ;;
  esac
done

# ------------------------------------------------------------------------------------------ utilidades
FALLAS=0; AVISOS=0
ok()    { printf '  [ OK    ] %s\n' "$*"; }
falla() { printf '  [ FALLA ] %s\n' "$*"; FALLAS=$((FALLAS + 1)); }
aviso() { printf '  [ AVISO ] %s\n' "$*"; AVISOS=$((AVISOS + 1)); }
info()  { printf '  %s\n' "$*"; }
paso()  { printf '\n== %s\n' "$*"; }
die()   { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

LOG_TMP=""
PW=""
cleanup() { [ -n "$LOG_TMP" ] && rm -f "$LOG_TMP"; PW=""; }
trap cleanup EXIT

# Valor de CLAVE en un archivo .env (ultima aparicion; sin comillas ni CR). No ejecuta el archivo.
envget() {
  local file="$1" key="$2"
  [ -f "$file" ] || return 0
  grep -E "^${key}=" "$file" | tail -n 1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

# Fija CLAVE en .env con el valor de la variable de entorno AVZ_VAL (nunca por argv). Uso: AVZ_VAL="$v" envset CLAVE
envset() {
  local key="$1" tmp
  tmp="$(mktemp "$ROOT/.env.XXXXXX")" || die "no se pudo crear un temporal en $ROOT"
  chmod 600 "$tmp"
  KEY="$key" awk '
    BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["AVZ_VAL"]; hecho = 0 }
    index($0, k "=") == 1 { if (!hecho) { print k "=" v; hecho = 1 } ; next }
    { print }
    END { if (!hecho) print k "=" v }' "$ENV_FILE" > "$tmp" && mv "$tmp" "$ENV_FILE" || { rm -f "$tmp"; die "no se pudo actualizar .env"; }
  chmod 600 "$ENV_FILE"
}

hex_aleatorio() { od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'; }          # $1 = bytes (salida = 2x caracteres)
fernet_aleatoria() { head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n'; }   # 32 bytes en base64 url-safe

ip2int() { local IFS=.; set -- $1; echo $(( ($1 << 24) + ($2 << 16) + ($3 << 8) + $4 )); }
int2ip() { local n="$1"; echo "$(( (n >> 24) & 255 )).$(( (n >> 16) & 255 )).$(( (n >> 8) & 255 )).$(( n & 255 ))"; }
mask_de() { echo $(( (0xFFFFFFFF << (32 - $1)) & 0xFFFFFFFF )); }
cidr_valido() {
  local c="$1" ip p o
  [[ "$c" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$ ]] || return 1
  ip="${c%/*}"; p="${c#*/}"
  for o in ${ip//./ }; do [ "$o" -le 255 ] || return 1; done
  [ "$p" -ge 16 ] && [ "$p" -le 29 ]
}
cidr_solapa() {  # cidr_solapa A/p B/q -> 0 si se solapan
  local a="${1%/*}" pa="${1#*/}" b="${2%/*}" pb="${2#*/}" p m
  p=$(( pa < pb ? pa : pb )); m="$(mask_de "$p")"
  [ $(( $(ip2int "$a") & m )) -eq $(( $(ip2int "$b") & m )) ]
}

puerto_ocupado() {  # 0 si algo escucha en TCP/$1
  local p="$1"
  if command -v ss >/dev/null 2>&1; then
    [ -n "$(ss -H -ltn "sport = :$p" 2>/dev/null)" ]
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null
  fi
}

http_estado() {  # http_estado URL -> codigo HTTP (000 si no responde)
  local url="$1" host port path
  if command -v curl >/dev/null 2>&1; then
    curl -s -o /dev/null -m 5 -w '%{http_code}' "$url" 2>/dev/null || echo 000
    return
  fi
  host="${url#http://}"; path="/${host#*/}"; host="${host%%/*}"; port="${host#*:}"; host="${host%:*}"
  ( exec 3<>"/dev/tcp/$host/$port" && printf 'GET %s HTTP/1.0\r\nHost: %s\r\n\r\n' "$path" "$host" >&3 && head -n 1 <&3 | awk '{print $2}' ) 2>/dev/null || echo 000
}

dc() { docker compose -p "$PROYECTO" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }

# Ejecuta un comando guardando su salida en un log temporal; si falla, muestra el final del log.
ejecutar() {
  local desc="$1"; shift
  LOG_TMP="$(mktemp)"; chmod 600 "$LOG_TMP"
  printf '  ... %s\n' "$desc"
  if "$@" >"$LOG_TMP" 2>&1; then
    rm -f "$LOG_TMP"; LOG_TMP=""; return 0
  fi
  printf '  [ FALLA ] %s. Ultimas lineas:\n' "$desc"
  tail -n 25 "$LOG_TMP" | sed 's/^/      /'
  rm -f "$LOG_TMP"; LOG_TMP=""
  return 1
}

estado_contenedor() {  # estado_contenedor SERVICIO -> healthy|unhealthy|starting|running|exited|...|ausente
  local cid
  cid="$(dc ps -q "$1" 2>/dev/null | head -n 1)"
  [ -n "$cid" ] || { echo ausente; return; }
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo ausente
}

esperar_estado() {  # esperar_estado SERVICIO ESTADO SEGUNDOS
  local svc="$1" want="$2" max="$3" t=0 st=""
  while [ "$t" -lt "$max" ]; do
    st="$(estado_contenedor "$svc")"
    [ "$st" = "$want" ] && return 0
    sleep 3; t=$((t + 3))
  done
  return 1
}

# ------------------------------------------------------------------------------------------ resolucion de valores
FUENTE_ENV="$ENV_FILE"; [ -f "$ENV_FILE" ] || FUENTE_ENV="$ENV_EXAMPLE"

PROYECTO="${PROYECTO_FLAG:-$(envget "$ENV_FILE" COMPOSE_PROJECT_NAME)}"; PROYECTO="${PROYECTO:-mikrotik-manager}"
API_PORT="${API_PORT_FLAG:-$(envget "$ENV_FILE" API_PORT)}"; API_PORT="${API_PORT:-8090}"
WEB_PORT="${WEB_PORT_FLAG:-$(envget "$ENV_FILE" WEB_PORT)}"; WEB_PORT="${WEB_PORT:-8091}"
API_BIND="$(envget "$ENV_FILE" API_BIND)"; API_BIND="${API_BIND:-127.0.0.1}"
WEB_BIND="$(envget "$ENV_FILE" WEB_BIND)"; WEB_BIND="${WEB_BIND:-127.0.0.1}"
SUBRED="${SUBRED_FLAG:-$(envget "$ENV_FILE" AVZ_EDGE_SUBNET)}"; SUBRED="${SUBRED:-172.29.91.0/24}"
HUB_HOST="$(envget "$FUENTE_ENV" HUB_HOST)"
VOLUMEN="${PROYECTO}_postgres_data"

echo "AVZCloud Central Manager - instalacion limpia"
echo "  proyecto=$PROYECTO  api=${API_BIND}:${API_PORT}  web=${WEB_BIND}:${WEB_PORT}  subred=$SUBRED"

# ------------------------------------------------------------------------------------------ (a) prerrequisitos
paso "Prerrequisitos"

[[ "$PROYECTO" =~ ^[a-z0-9][a-z0-9_-]*$ ]] && ok "nombre de proyecto valido ($PROYECTO)" || falla "nombre de proyecto invalido (use minusculas, digitos, - y _)"

[ -f "$COMPOSE_FILE" ] && [ -f "$ROOT/backend/Dockerfile" ] && [ -f "$ROOT/frontend-new/Dockerfile" ] \
  && ok "codigo fuente presente (backend/, frontend-new/, deploy/docker-compose.yml)" \
  || falla "faltan backend/Dockerfile, frontend-new/Dockerfile o deploy/docker-compose.yml: ejecute el script desde una copia completa del repositorio"
[ -f "$ENV_FILE" ] || [ -f "$ENV_EXAMPLE" ] && ok ".env o .env.example disponible" || falla "no existe ni .env ni .env.example en $ROOT"

DOCKER_OK=0; DOCKER_USABLE=0
if command -v docker >/dev/null 2>&1; then
  if v="$(docker compose version --short 2>/dev/null)" && [[ "$v" =~ ^v?[2-9] ]]; then
    ok "Docker y Compose v2 ($v)"; DOCKER_OK=1
  else
    falla "Docker esta instalado pero falta el plugin Compose v2 (docker compose version). En Ubuntu: apt install docker-compose-plugin"
  fi
else
  falla "Docker no esta instalado (ver INSTALACION-NUEVO-SERVIDOR.md, seccion 1)"
fi

if [ "$DOCKER_OK" = 1 ]; then
  if docker info >/dev/null 2>&1; then
    ok "permisos para usar Docker (usuario $(id -un))"; DOCKER_USABLE=1
  else
    falla "este usuario no puede usar Docker: ejecute como root o agregue el usuario al grupo docker (y vuelva a iniciar sesion)"
  fi
fi

command -v base64 >/dev/null 2>&1 && [ -r /dev/urandom ] && ok "herramientas para generar secretos (od, base64, /dev/urandom)" || falla "falta base64 o /dev/urandom (paquete coreutils)"

# Puertos locales
if [ "$API_PORT" = "$WEB_PORT" ]; then
  falla "API_PORT y WEB_PORT son iguales ($API_PORT)"
else
  for par in "API:$API_PORT" "WEB:$WEB_PORT"; do
    nombre="${par%%:*}"; p="${par#*:}"
    if ! [[ "$p" =~ ^[0-9]+$ ]] || [ "$p" -lt 1024 ] || [ "$p" -gt 65535 ]; then
      falla "puerto $nombre invalido: $p (use 1024-65535)"
    elif puerto_ocupado "$p"; then
      propio=""
      [ "$DOCKER_USABLE" = 1 ] && propio="$(docker ps -q --filter "label=com.docker.compose.project=$PROYECTO" --filter status=running 2>/dev/null | head -n 1)"
      if [ -n "$propio" ]; then ok "puerto $nombre $p en uso por este mismo stack ($PROYECTO): se reutiliza"
      else falla "puerto $nombre $p ocupado por otro proceso: elija otro con --api-port / --web-port"; fi
    else
      ok "puerto $nombre $p libre"
    fi
  done
fi

# Subred
if ! cidr_valido "$SUBRED"; then
  falla "subred invalida: $SUBRED (formato A.B.C.D/N con N entre 16 y 29)"
else
  SUBRED="$(int2ip $(( $(ip2int "${SUBRED%/*}") & $(mask_de "${SUBRED#*/}") )))/${SUBRED#*/}"   # normaliza a la direccion de red
  choque=""
  if [ "$DOCKER_USABLE" = 1 ]; then
    while read -r nombre subredes; do
      [ "$nombre" = "${PROYECTO}_edge-net" ] && continue          # la red de este mismo stack no cuenta
      for s in $subredes; do
        [[ "$s" == *:* ]] && continue
        cidr_valido_ip="${s%/*}"; [[ "$cidr_valido_ip" =~ ^[0-9.]+$ ]] || continue
        cidr_solapa "$SUBRED" "$s" && choque="$choque red-docker:$nombre($s)"
      done
    done < <(docker network ls -q 2>/dev/null | xargs -r docker network inspect --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null)
  fi
  if command -v ip >/dev/null 2>&1; then
    while read -r _ ifc _ dir _; do
      case "$ifc" in docker0|br-*|veth*) continue ;; esac
      [[ "$dir" =~ ^[0-9.]+/[0-9]+$ ]] || continue
      cidr_solapa "$SUBRED" "$dir" && choque="$choque interfaz:$ifc($dir)"
    done < <(ip -4 -o addr show scope global 2>/dev/null)
  fi
  if [ -n "$choque" ]; then falla "la subred $SUBRED choca con:$choque -> use --subred con otra (p. ej. 172.29.92.0/24)"
  else ok "subred $SUBRED libre (sin choque con redes Docker ni interfaces del host)"; fi
fi

# Espacio en disco (la raiz de Docker, o / si no se sabe)
RAIZ_DOCKER="/"
[ "$DOCKER_USABLE" = 1 ] && RAIZ_DOCKER="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /)"; [ -d "$RAIZ_DOCKER" ] || RAIZ_DOCKER="/"
libre_kb="$(df -Pk "$RAIZ_DOCKER" 2>/dev/null | awk 'NR==2 {print $4}')"
if [[ "$libre_kb" =~ ^[0-9]+$ ]]; then
  libre_gb=$((libre_kb / 1024 / 1024))
  if [ "$libre_kb" -lt 3145728 ]; then falla "espacio libre insuficiente en $RAIZ_DOCKER: ${libre_gb} GB (minimo 3 GB para construir las imagenes)"
  elif [ "$libre_kb" -lt 5242880 ]; then aviso "poco espacio libre en $RAIZ_DOCKER: ${libre_gb} GB (recomendado 5 GB o mas)"
  else ok "espacio libre en $RAIZ_DOCKER: ${libre_gb} GB"; fi
else
  aviso "no se pudo medir el espacio libre en $RAIZ_DOCKER"
fi

# Ruta al hub
if [ "$SIN_HUB" = 1 ]; then
  info "[ OMITIDO ] ruta al hub (--sin-hub): aprovisionamiento, VPN y monitoreo no funcionaran hasta configurarlo"
elif [ -z "$HUB_HOST" ]; then
  falla "HUB_HOST vacio en $(basename "$FUENTE_ENV"); defina la IP privada del hub o use --sin-hub"
elif ! command -v ip >/dev/null 2>&1; then
  aviso "no existe el comando ip; no se puede comprobar la ruta hacia el hub $HUB_HOST"
elif ruta="$(ip -4 route get "$HUB_HOST" 2>/dev/null | head -n 1)" && [ -n "$ruta" ]; then
  dev="$(echo "$ruta" | sed -n 's/.* dev \([^ ]*\).*/\1/p')"
  dev_defecto="$(ip -4 route show default 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -n 1)"
  if [ -n "$dev" ] && [ "$dev" = "$dev_defecto" ] && ! echo "$ruta" | grep -q 'scope link'; then
    aviso "hay ruta hacia el hub $HUB_HOST pero sale por la interfaz por defecto ($dev): deberia ser el enlace privado"
  else
    ok "ruta hacia el hub $HUB_HOST por la interfaz $dev"
  fi
else
  falla "no hay ruta hacia el hub $HUB_HOST: configure el enlace privado (INSTALACION-NUEVO-SERVIDOR.md, seccion 3) o use --sin-hub"
fi

if [ "$FALLAS" -gt 0 ]; then
  printf '\nPrerrequisitos: %s FALLA(S), %s aviso(s). Corrija y vuelva a ejecutar (no se hizo ningun cambio).\n' "$FALLAS" "$AVISOS"
  exit 1
fi
printf '\nPrerrequisitos: todo en orden (%s aviso(s)).\n' "$AVISOS"
[ "$SOLO_VERIFICAR" = 1 ] && exit 0

# ------------------------------------------------------------------------------------------ (b) .env y secretos
paso "Archivo .env y secretos"

if [ ! -f "$ENV_FILE" ]; then
  (umask 077; cp "$ENV_EXAMPLE" "$ENV_FILE") || die "no se pudo crear $ENV_FILE"
  chmod 600 "$ENV_FILE"
  ok ".env creado desde .env.example (permisos 600)"
  AVZ_VAL="$(date +%Y%m%d%H%M)" envset ASSET_VERSION
else
  ok ".env ya existe: se conserva (solo se completan las claves vacias)"
fi
if grep -q $'\r' "$ENV_FILE"; then
  tr -d '\r' < "$ENV_FILE" > "$ENV_FILE.tmp" && chmod 600 "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
  aviso ".env tenia finales de linea CRLF: se convirtieron a LF"
fi
modo="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo ?)"
if [ "$modo" != "600" ]; then chmod 600 "$ENV_FILE"; aviso ".env tenia permisos $modo: ajustado a 600"; fi

# Si ya existe el volumen de datos, no se pueden inventar credenciales nuevas.
VOL_EXISTE=0
docker volume inspect "$VOLUMEN" >/dev/null 2>&1 && VOL_EXISTE=1

generados=""
asegurar() {  # asegurar CLAVE TIPO  (TIPO: hex24|hex32|fernet|literal:valor) -> genera solo si esta vacia
  local key="$1" tipo="$2" actual
  actual="$(envget "$ENV_FILE" "$key")"
  [ -n "$actual" ] && return 0
  case "$tipo" in
    hex24)   AVZ_VAL="$(hex_aleatorio 24)" envset "$key" ;;
    hex32)   AVZ_VAL="$(hex_aleatorio 32)" envset "$key" ;;
    fernet)  AVZ_VAL="$(fernet_aleatoria)" envset "$key" ;;
    literal:*) AVZ_VAL="${tipo#literal:}" envset "$key" ;;
  esac
  generados="$generados $key"
}

if [ "$VOL_EXISTE" = 1 ]; then
  for k in POSTGRES_PASSWORD VAULT_KEY POSTGRES_DB POSTGRES_USER; do
    [ -n "$(envget "$ENV_FILE" "$k")" ] || die "existe el volumen $VOLUMEN pero $k esta vacia en .env. No se inventa un valor nuevo (no coincidiria con los datos). Restaure el .env original (copia de respaldo) o complete $k a mano."
  done
fi
asegurar POSTGRES_DB literal:avzcloud
asegurar POSTGRES_USER literal:avzcloud
asegurar POSTGRES_PASSWORD hex24
asegurar SECRET_KEY hex32
asegurar VAULT_KEY fernet
if [ -n "$generados" ]; then
  ok "valores generados (no se muestran):$generados"
else
  ok "todos los secretos ya existian en .env: no se regeneran"
fi

# Parametros de esta instalacion (los flags tienen prioridad y quedan guardados para los comandos manuales).
GATEWAY="$(envget "$ENV_FILE" AVZ_EDGE_GATEWAY)"
if [ -n "$SUBRED_FLAG" ] || [ "$(envget "$ENV_FILE" AVZ_EDGE_SUBNET)" != "$SUBRED" ] || [ -z "$GATEWAY" ]; then
  GATEWAY="$(int2ip $(( $(ip2int "${SUBRED%/*}") + 1 )))"
fi
AVZ_VAL="$PROYECTO" envset COMPOSE_PROJECT_NAME
AVZ_VAL="$API_PORT" envset API_PORT
AVZ_VAL="$WEB_PORT" envset WEB_PORT
AVZ_VAL="$SUBRED" envset AVZ_EDGE_SUBNET
AVZ_VAL="$GATEWAY" envset AVZ_EDGE_GATEWAY
info "guardado en .env: COMPOSE_PROJECT_NAME, API_PORT, WEB_PORT, AVZ_EDGE_SUBNET, AVZ_EDGE_GATEWAY"

# ------------------------------------------------------------------------------------------ (c) build y base de datos
paso "Construccion de imagenes y base de datos"

ejecutar "docker compose config (validacion, sin mostrar valores)" dc --profile tools config -q || die "el compose no es valido; revise .env (falta alguna variable obligatoria)"
ejecutar "construyendo imagenes (la primera vez tarda unos minutos)" dc --profile tools build || die "fallo la construccion de las imagenes"
ok "imagenes construidas"
ejecutar "levantando la base de datos" dc up -d mikrotik-db || die "no se pudo levantar la base de datos"
printf '  ... esperando a que la base este sana (hasta 120 s)\n'
if esperar_estado mikrotik-db healthy 120; then ok "base de datos sana"
else
  dc logs --tail 20 mikrotik-db 2>&1 | sed 's/^/      /'
  die "la base no llego a estado healthy. Si el log habla de permisos o read-only, ver DEPLOY.md 'Si la BD no arranca'."
fi

# ------------------------------------------------------------------------------------------ (d) check-env y migraciones
paso "Verificacion de variables y migraciones"

SALIDA_CHK="$(dc --profile tools run --rm -T cli check-env 2>&1)"
echo "$SALIDA_CHK" | sed 's/^/  /'
if echo "$SALIDA_CHK" | grep -E '^FALTA' | grep -vq -E '^FALTA +HUB_'; then
  die "faltan variables obligatorias (listadas arriba). Complete $ENV_FILE y vuelva a ejecutar."
fi
if echo "$SALIDA_CHK" | grep -q -E 'no arrancara|Fernet'; then
  die "SECRET_KEY o VAULT_KEY invalidas (ver avisos arriba)."
fi
if echo "$SALIDA_CHK" | grep -q -E '^FALTA +HUB_'; then
  aviso "faltan variables del hub (HUB_*): complete .env antes de aprovisionar routers (tabla de variables en INSTALACION-NUEVO-SERVIDOR.md)"
fi

if ejecutar "aplicando migraciones" dc --profile tools run --rm -T cli migrate; then
  ok "migraciones al dia"
else
  die "fallo la migracion"
fi

# ------------------------------------------------------------------------------------------ (e) superadmin
paso "Superadmin"

crear_admin() {  # lee $PW y lo pasa SOLO por variable de entorno al contenedor
  ADMIN_BOOTSTRAP_PASSWORD="$PW" dc --profile tools run --rm -T cli set-admin-password 2>&1 | sed 's/^/  /'
  return "${PIPESTATUS[0]}"
}

if [ "$NO_SUPERADMIN" = 1 ]; then
  info "[ OMITIDO ] --no-superadmin. Para crearlo despues: ver INSTALACION-NUEVO-SERVIDOR.md, seccion 5."
else
  existe="$(echo "SELECT count(*) FROM usuarios WHERE lower(email) = 'admin@avzcloud.com'" \
    | dc exec -T mikrotik-db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' 2>/dev/null | tr -d '[:space:]')"
  if [ -n "$ADMIN_PW_FILE" ]; then
    [ -r "$ADMIN_PW_FILE" ] || die "no se puede leer --admin-password-file: $ADMIN_PW_FILE"
    mp="$(stat -c '%a' "$ADMIN_PW_FILE" 2>/dev/null || echo 600)"
    case "$mp" in *00) ;; *) aviso "el archivo de contrasena tiene permisos $mp; deberia ser 600" ;; esac
    IFS= read -r PW < "$ADMIN_PW_FILE" || true
    [ -n "$PW" ] || die "el archivo de contrasena esta vacio"
    crear_admin && ok "superadmin establecido desde el archivo (se exigira cambio de contrasena al primer acceso)" || die "no se pudo establecer el superadmin (ver mensaje; politica: 10+ caracteres con mayuscula, minuscula, numero y simbolo)"
    PW=""
  elif [ "$existe" = "1" ]; then
    ok "el superadmin ya existe: no se toca (para restablecerlo use --admin-password-file)"
  elif [ -t 0 ]; then
    echo "  Contrasena del superadmin admin@avzcloud.com (10+ caracteres con mayuscula, minuscula, numero y simbolo)."
    hecho=0
    for intento in 1 2 3; do
      read -rsp "  Contrasena: " PW; echo
      read -rsp "  Repita la contrasena: " PW2; echo
      if [ -z "$PW" ] || [ "$PW" != "$PW2" ]; then echo "  No coinciden o esta vacia."; PW=""; PW2=""; continue; fi
      PW2=""
      if crear_admin; then hecho=1; PW=""; break; fi
      PW=""
    done
    [ "$hecho" = 1 ] && ok "superadmin creado (se exigira cambio de contrasena al primer acceso)" || aviso "no se creo el superadmin; repita con --admin-password-file o ver el documento, seccion 5"
  else
    aviso "sin terminal interactiva ni --admin-password-file: no se creo el superadmin (ver INSTALACION-NUEVO-SERVIDOR.md, seccion 5)"
  fi
fi

# ------------------------------------------------------------------------------------------ (f) API y web
paso "Arranque de API y web"

ejecutar "levantando API y web" dc up -d mikrotik-api mikrotik-frontend || die "no se pudieron levantar la API y la web"
printf '  ... esperando a que la API este sana (hasta 180 s)\n'
if esperar_estado mikrotik-api healthy 180; then ok "contenedor de la API sano"
else
  dc logs --tail 25 mikrotik-api 2>&1 | sed 's/^/      /'
  die "la API no llego a healthy"
fi
esperar_estado mikrotik-frontend healthy 60 && ok "contenedor de la web sano" || aviso "la web no reporta healthy todavia (dc ps)"

HOST_API="$API_BIND"; case "$HOST_API" in 0.0.0.0|"") HOST_API=127.0.0.1 ;; esac
HOST_WEB="$WEB_BIND"; case "$HOST_WEB" in 0.0.0.0|"") HOST_WEB=127.0.0.1 ;; esac
SALUD="000"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  SALUD="$(http_estado "http://${HOST_API}:${API_PORT}/api/v1/health")"
  [ "$SALUD" = "200" ] && break
  sleep 3
done
[ "$SALUD" = "200" ] && ok "GET /api/v1/health -> 200" || falla "GET /api/v1/health -> $SALUD (se esperaba 200)"
WEB_EST="$(http_estado "http://${HOST_WEB}:${WEB_PORT}/healthz")"
[ "$WEB_EST" = "200" ] && ok "GET /healthz (web) -> 200" || falla "GET /healthz (web) -> $WEB_EST (se esperaba 200)"

# ------------------------------------------------------------------------------------------ (g) resumen
paso "Resumen"
echo "  Proyecto ........ $PROYECTO"
echo "  API local ....... http://${HOST_API}:${API_PORT}   (salud: $SALUD)"
echo "  Web local ....... http://${HOST_WEB}:${WEB_PORT}   (salud: $WEB_EST)"
echo "  Volumen de datos  $VOLUMEN"
echo "  Archivo .env .... $ENV_FILE (600)"
[ -n "$DOMINIO" ] && echo "  Dominio previsto  $DOMINIO"
echo
echo "  Pasos manuales que quedan (detalle en deploy/INSTALACION-NUEVO-SERVIDOR.md):"
echo "   1. Copiar VAULT_KEY y SECRET_KEY del .env al gestor de secretos de AVZ. Sin VAULT_KEY no se"
echo "      pueden recuperar las claves WireGuard cifradas en la BD. Leerlas en una terminal privada."
echo "   2. Proxy de entrada con TLS${DOMINIO:+ para $DOMINIO}: / -> ${HOST_WEB}:${WEB_PORT} y /api/ -> ${HOST_API}:${API_PORT},"
echo "      con X-Forwarded-For (ejemplo en el documento, seccion 2)."
echo "   3. DNS${DOMINIO:+ de $DOMINIO} apuntando al servidor."
if [ "$SIN_HUB" = 1 ]; then
  echo "   4. Enlace privado al hub y regla del hub para la IP de este servidor; completar HUB_* en .env y"
  echo "      ejecutar: docker compose -p $PROYECTO --env-file $ENV_FILE -f $COMPOSE_FILE up -d mikrotik-api"
else
  echo "   4. Regla del hub (API 8728/tcp) para la IP privada de este servidor; completar HUB_API_USER,"
  echo "      HUB_API_PASSWORD, HUB_WG_INTERFACE y HUB_PUBLIC_ENDPOINT en .env y recrear la API."
fi
echo "   5. Configurar SMTP en la interfaz (se guarda en la BD) y, si aplica, el acceso de administracion por Tailscale."
echo "   6. Programar respaldos: deploy/respaldo.sh --proyecto $PROYECTO (cron diario) y guardar copias fuera del servidor."
echo

if [ "$FALLAS" -gt 0 ]; then
  echo "Instalacion terminada CON FALLAS en la verificacion final. Revise: docker compose -p $PROYECTO --env-file $ENV_FILE -f $COMPOSE_FILE logs --tail 50"
  exit 1
fi
echo "Instalacion completada correctamente."
exit 0
