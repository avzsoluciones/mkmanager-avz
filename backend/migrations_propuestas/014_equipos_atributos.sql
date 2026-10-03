-- BORRADOR SIN PROBAR (arquitecto de datos, 2026-10-03). NO se aplica: el ejecutor solo lee backend/migrations.
-- Depende de 010 (usa el helper _avz_add_constraint). Confirmar los valores de arquitectura contra /system/resource de un equipo real.
-- 014: atributos de equipo y edicion.
ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS arquitectura VARCHAR(10);
ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS soporta_contenedores BOOLEAN;     -- NULL = desconocido
ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS version_os VARCHAR(30);
ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS notas TEXT;
ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now();

SELECT _avz_add_constraint('dispositivos', 'ck_disp_arquitectura',
  $c$CHECK (arquitectura IS NULL OR arquitectura IN ('mipsbe','smips','mmips','arm','arm64','x86','ppc','tile'))$c$);
SELECT _avz_add_constraint('dispositivos', 'ck_disp_contenedores',
  $c$CHECK (soporta_contenedores IS NOT TRUE OR arquitectura IS NULL OR arquitectura IN ('arm','arm64','x86'))$c$);
SELECT _avz_add_constraint('dispositivos', 'ck_disp_notas', $c$CHECK (notas IS NULL OR char_length(notas) <= 2000)$c$);

WITH d AS (SELECT id, row_number() OVER (PARTITION BY sede_id, lower(nombre) ORDER BY id) AS rn FROM dispositivos)
UPDATE dispositivos x SET nombre = left(x.nombre, 110) || ' (' || x.id || ')' FROM d WHERE d.id = x.id AND d.rn > 1;
CREATE UNIQUE INDEX IF NOT EXISTS uq_dispositivos_sede_nombre ON dispositivos (sede_id, lower(nombre));
