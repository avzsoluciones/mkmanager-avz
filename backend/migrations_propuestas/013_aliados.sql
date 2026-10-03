-- BORRADOR SIN PROBAR (arquitecto de datos, 2026-10-03). NO se aplica: el ejecutor solo lee backend/migrations.
-- RIESGO ALTO: debe ir en la MISMA version que el codigo que la usa (security.py, empresas.py, users.py,
-- monitoring.py, create_empresa debe escribir aliado_id). Depende de 010. Supuestos a confirmar con datos reales:
--   partner_id NULL = AVZ Cloud; cliente sin aliado = AVZ Cloud (CONFIRMADO por el usuario: AVZ Cloud es un aliado con sus propias empresas).
-- 013: aliados como entidad propia. Expandir: se conservan partner_id/tenant_id (sin uso) hasta la 016.
CREATE TABLE IF NOT EXISTS aliados (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(150) NOT NULL,                       -- nombre visible
    razon_social VARCHAR(200),
    contacto_email VARCHAR(150),
    email_alertas VARCHAR(150),                         -- buzon al que van las alertas de sus equipos
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_aliados_nombre CHECK (btrim(nombre) <> '')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_aliados_nombre ON aliados (lower(nombre));

-- 1. Un aliado por cada cuenta principal; conserva el id del usuario para mapear sin traducir.
INSERT INTO aliados (id, nombre, contacto_email, email_alertas, activo)
SELECT u.id,
       CASE WHEN row_number() OVER (PARTITION BY lower(btrim(u.nombre_completo)) ORDER BY u.id) = 1
            THEN btrim(u.nombre_completo)
            ELSE left(btrim(u.nombre_completo), 140) || ' (' || u.id || ')' END,
       u.email, u.email, COALESCE(u.activo, TRUE)
FROM usuarios u
WHERE u.rol = 'partner' AND u.tenant_id = u.id
ON CONFLICT DO NOTHING;

SELECT setval(pg_get_serial_sequence('aliados', 'id'),
              GREATEST((SELECT COALESCE(MAX(id), 0) FROM aliados), 1),
              (SELECT COUNT(*) > 0 FROM aliados));

-- AVZ Cloud tambien es aliado (sustituye al partner_id NULL).
INSERT INTO aliados (nombre, contacto_email)
SELECT 'AVZ Cloud', NULL WHERE NOT EXISTS (SELECT 1 FROM aliados WHERE lower(nombre) = 'avz cloud');

-- 2. Empresas -> aliado (SUPUESTO: partner_id NULL = AVZ Cloud)
ALTER TABLE empresas ADD COLUMN IF NOT EXISTS aliado_id INTEGER REFERENCES aliados(id) ON DELETE RESTRICT;
UPDATE empresas e SET aliado_id = COALESCE(
    (SELECT a.id FROM aliados a WHERE a.id = (SELECT COALESCE(u.tenant_id, u.id) FROM usuarios u WHERE u.id = e.partner_id)),
    (SELECT id FROM aliados WHERE lower(nombre) = 'avz cloud'))
 WHERE e.aliado_id IS NULL;
ALTER TABLE empresas ALTER COLUMN aliado_id SET NOT NULL;

WITH d AS (SELECT id, row_number() OVER (PARTITION BY aliado_id, lower(nombre) ORDER BY id) AS rn FROM empresas)
UPDATE empresas e SET nombre = left(e.nombre, 140) || ' (' || e.id || ')' FROM d WHERE d.id = e.id AND d.rn > 1;
CREATE UNIQUE INDEX IF NOT EXISTS uq_empresas_aliado_nombre ON empresas (aliado_id, lower(nombre));
CREATE UNIQUE INDEX IF NOT EXISTS uq_empresas_id_aliado ON empresas (id, aliado_id);   -- destino de la FK compuesta
CREATE INDEX IF NOT EXISTS idx_empresas_aliado ON empresas (aliado_id);
-- El indice viejo usa partner_id; con partner_id sin escribir colisionaria entre aliados. Se reemplaza.
DROP INDEX IF EXISTS uq_empresas_nombre;

-- 3. Sedes: aliado heredado de la empresa y atado por FK compuesta
ALTER TABLE sedes ADD COLUMN IF NOT EXISTS aliado_id INTEGER;
UPDATE sedes s SET aliado_id = e.aliado_id FROM empresas e WHERE e.id = s.empresa_id AND s.aliado_id IS DISTINCT FROM e.aliado_id;
ALTER TABLE sedes ALTER COLUMN aliado_id SET NOT NULL;
CREATE OR REPLACE FUNCTION sedes_heredar_aliado() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  SELECT aliado_id INTO NEW.aliado_id FROM empresas WHERE id = NEW.empresa_id;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_sedes_aliado ON sedes;
CREATE TRIGGER trg_sedes_aliado BEFORE INSERT ON sedes FOR EACH ROW EXECUTE FUNCTION sedes_heredar_aliado();
-- Solo en INSERT: mover una sede a la empresa de OTRO aliado falla por la FK compuesta (fuera de la v1).
SELECT _avz_add_constraint('sedes', 'fk_sedes_empresa_aliado',
  $c$FOREIGN KEY (empresa_id, aliado_id) REFERENCES empresas (id, aliado_id) ON DELETE RESTRICT ON UPDATE RESTRICT$c$);
CREATE INDEX IF NOT EXISTS idx_sedes_aliado ON sedes (aliado_id);

-- 3b. Destino de la FK compuesta usuario -> sede de su empresa (alcance de una sola sede)
CREATE UNIQUE INDEX IF NOT EXISTS uq_sedes_id_empresa ON sedes (id, empresa_id);

-- 4. Usuarios: aliado y, para clientes, su empresa y opcionalmente UNA sede (decision del usuario: se elige al crear)
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS aliado_id INTEGER REFERENCES aliados(id) ON DELETE RESTRICT;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS empresa_id INTEGER REFERENCES empresas(id) ON DELETE RESTRICT;
UPDATE usuarios u SET aliado_id = a.id FROM aliados a
 WHERE u.aliado_id IS NULL AND u.rol IN ('partner','tecnico')
   AND a.id = COALESCE(u.tenant_id, CASE WHEN u.rol = 'partner' THEN u.id END);
UPDATE usuarios u SET aliado_id = COALESCE(
    (SELECT a.id FROM aliados a WHERE a.id = (SELECT COALESCE(p.tenant_id, p.id) FROM usuarios p WHERE p.id = u.partner_id)),
    (SELECT id FROM aliados WHERE lower(nombre) = 'avz cloud'))      -- SUPUESTO: cliente sin aliado = AVZ Cloud
 WHERE u.aliado_id IS NULL AND u.rol = 'cliente';
UPDATE usuarios u SET empresa_id = (SELECT s.empresa_id FROM sedes s WHERE s.cliente_id = u.id ORDER BY s.id LIMIT 1)
 WHERE u.rol = 'cliente' AND u.empresa_id IS NULL;
-- Si algun partner o tecnico no se pudo mapear, la restriccion siguiente aborta la migracion a proposito.
SELECT _avz_add_constraint('usuarios', 'ck_usuarios_alcance',
  $c$CHECK ((rol IN ('superadmin','soporte') AND aliado_id IS NULL)
         OR (rol IN ('partner','tecnico','cliente') AND aliado_id IS NOT NULL))$c$);
SELECT _avz_add_constraint('usuarios', 'ck_usuarios_empresa', $c$CHECK (empresa_id IS NULL OR rol = 'cliente')$c$);
SELECT _avz_add_constraint('usuarios', 'fk_usuarios_empresa_aliado',
  $c$FOREIGN KEY (empresa_id, aliado_id) REFERENCES empresas (id, aliado_id) ON DELETE RESTRICT$c$);
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS sede_id INTEGER REFERENCES sedes(id) ON DELETE RESTRICT;   -- NULL = todas las sedes de su empresa
UPDATE usuarios u SET sede_id = (SELECT s.id FROM sedes s WHERE s.cliente_id = u.id ORDER BY s.id LIMIT 1)
 WHERE u.rol = 'cliente' AND u.sede_id IS NULL AND (SELECT count(*) FROM sedes s WHERE s.cliente_id = u.id) = 1
   AND (SELECT count(*) FROM sedes s2 WHERE s2.empresa_id = u.empresa_id) > 1;   -- conserva el alcance de una sola sede ya existente
SELECT _avz_add_constraint('usuarios', 'ck_usuarios_sede', $c$CHECK (sede_id IS NULL OR (rol = 'cliente' AND empresa_id IS NOT NULL))$c$);
SELECT _avz_add_constraint('usuarios', 'fk_usuarios_sede_empresa',
  $c$FOREIGN KEY (sede_id, empresa_id) REFERENCES sedes (id, empresa_id) ON DELETE RESTRICT$c$);
CREATE INDEX IF NOT EXISTS idx_usuarios_sede ON usuarios (sede_id) WHERE sede_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_usuarios_aliado ON usuarios (aliado_id);
CREATE INDEX IF NOT EXISTS idx_usuarios_empresa ON usuarios (empresa_id) WHERE empresa_id IS NOT NULL;
