-- BORRADOR SIN PROBAR (arquitecto de datos, 2026-10-03). NO se aplica: el ejecutor solo lee backend/migrations.
-- Para activarla: probarla en el PostgreSQL desechable (skill diseno-base-datos, scripts/lab-migraciones.sh) y moverla a backend/migrations.
-- 010: integridad de la jerarquia (equipo -> sede -> empresa), estados y unicidades.
-- Idempotente. Las guardas abortan antes de tocar nada si hay datos que impedirian las restricciones.

CREATE OR REPLACE FUNCTION _avz_add_constraint(p_tabla regclass, p_nombre text, p_def text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = p_tabla AND conname = p_nombre) THEN
    EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I %s', p_tabla, p_nombre, p_def);
  END IF;
END $$;

CREATE OR REPLACE FUNCTION _avz_reemplazar_fk(p_tabla regclass, p_col text, p_ref regclass, p_accion text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE r record;
BEGIN
  FOR r IN SELECT c.conname FROM pg_constraint c
           JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
           WHERE c.conrelid = p_tabla AND c.contype = 'f' AND array_length(c.conkey, 1) = 1 AND a.attname = p_col
  LOOP
    EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', p_tabla, r.conname);
  END LOOP;
  EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %s(id) ON DELETE %s',
                 p_tabla, 'fk_' || p_tabla::text || '_' || p_col, p_col, p_ref, p_accion);
END $$;

-- 0. Guardas
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM dispositivos WHERE sede_id IS NULL) THEN
    RAISE EXCEPTION '010: hay equipos sin sede. Asignelos a una sede o denles de baja (con limpieza del hub) y reintente.';
  END IF;
  IF EXISTS (SELECT 1 FROM dispositivos WHERE lan_cidr IS NOT NULL GROUP BY lan_cidr HAVING count(*) > 1) THEN
    RAISE EXCEPTION '010: hay equipos con la misma lan_cidr.';
  END IF;
  IF EXISTS (SELECT 1 FROM wg_peers WHERE tipo = 'router' AND revocado_en IS NULL
             GROUP BY dispositivo_id HAVING count(*) > 1) THEN
    RAISE EXCEPTION '010: hay equipos con mas de un peer de router activo.';
  END IF;
END $$;

-- 1. Normalizar estados antes de poner CHECK
UPDATE dispositivos SET estado = CASE WHEN lower(estado) IN ('pendiente','online','offline') THEN lower(estado) ELSE 'pendiente' END
 WHERE estado IS NULL OR estado NOT IN ('pendiente','online','offline');
UPDATE sedes SET estado = CASE WHEN lower(estado) IN ('activa','inactiva') THEN lower(estado) ELSE 'activa' END
 WHERE estado IS NULL OR estado NOT IN ('activa','inactiva');

ALTER TABLE dispositivos ALTER COLUMN estado SET DEFAULT 'pendiente';
ALTER TABLE dispositivos ALTER COLUMN estado SET NOT NULL;
ALTER TABLE dispositivos ALTER COLUMN ultimo_heartbeat DROP DEFAULT;
ALTER TABLE sedes ALTER COLUMN estado SET NOT NULL;

SELECT _avz_add_constraint('dispositivos', 'ck_disp_estado', $c$CHECK (estado IN ('pendiente','online','offline'))$c$);
SELECT _avz_add_constraint('dispositivos', 'ck_disp_puerto', $c$CHECK (puerto_gestion IS NULL OR puerto_gestion BETWEEN 1 AND 65535)$c$);
SELECT _avz_add_constraint('dispositivos', 'ck_disp_tunnel_ip',
  $c$CHECK (tunnel_ip IS NULL OR tunnel_ip ~ '^10\.254\.0\.([2-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-4])$')$c$);
SELECT _avz_add_constraint('sedes', 'ck_sedes_estado', $c$CHECK (estado IN ('activa','inactiva'))$c$);
SELECT _avz_add_constraint('plantillas_aplicadas', 'ck_plantilla_estado',
  $c$CHECK (estado IN ('pendiente','aplicando','aplicada','confirmada','revertida','error','error_reversion'))$c$);
SELECT _avz_add_constraint('alertas', 'ck_alerta_cierre', $c$CHECK ((estado = 'cerrado') = (cerrado_en IS NOT NULL))$c$);

-- 2. Reglas de borrado alineadas con el negocio
ALTER TABLE dispositivos ALTER COLUMN sede_id SET NOT NULL;
SELECT _avz_reemplazar_fk('dispositivos', 'sede_id', 'sedes', 'RESTRICT');   -- no se borra una sede con equipos
SELECT _avz_reemplazar_fk('sedes', 'cliente_id', 'usuarios', 'SET NULL');    -- ya no arrastra sedes al borrar un usuario

-- 3. wg_peers
CREATE INDEX IF NOT EXISTS idx_wg_peers_dispositivo ON wg_peers(dispositivo_id) WHERE dispositivo_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_wg_peers_router_activo
    ON wg_peers(dispositivo_id) WHERE tipo = 'router' AND revocado_en IS NULL;
SELECT _avz_add_constraint('wg_peers', 'ck_wgpeer_tipo_ref',
  $c$CHECK ((tipo = 'router' AND dispositivo_id IS NOT NULL) OR (tipo = 'usuario' AND dispositivo_id IS NULL))$c$);
-- Un acceso VPN activo no puede perder su sede; los revocados si (el SET NULL del FK fallaria contra este CHECK).
SELECT _avz_add_constraint('wg_peers', 'ck_wgpeer_usuario_sede',
  $c$CHECK (tipo <> 'usuario' OR revocado_en IS NOT NULL OR sede_id IS NOT NULL)$c$);

-- 4. Unicidad de nombre de sede por empresa (sin distinguir mayusculas) y de LAN
WITH d AS (SELECT id, row_number() OVER (PARTITION BY empresa_id, lower(nombre) ORDER BY id) AS rn FROM sedes)
UPDATE sedes s SET nombre = left(s.nombre, 140) || ' (' || s.id || ')' FROM d WHERE d.id = s.id AND d.rn > 1;
CREATE UNIQUE INDEX IF NOT EXISTS uq_sedes_empresa_nombre ON sedes (empresa_id, lower(nombre));
CREATE UNIQUE INDEX IF NOT EXISTS ux_dispositivos_lan_cidr ON dispositivos(lan_cidr) WHERE lan_cidr IS NOT NULL;
