-- 006: motor de plantillas con reversión. Migración aditiva: no modifica columnas ni datos existentes.
-- ops / inversas: operaciones estructuradas de API RouterOS (ver services/ops.py), aplicadas y su deshacer.
-- Estados: aplicando | aplicada | confirmada | revertida | error | error_reversion.

ALTER TABLE plantillas_aplicadas ADD COLUMN IF NOT EXISTS ops JSONB;
ALTER TABLE plantillas_aplicadas ADD COLUMN IF NOT EXISTS inversas JSONB;
ALTER TABLE plantillas_aplicadas ADD COLUMN IF NOT EXISTS intentos_reversion INTEGER NOT NULL DEFAULT 0;
ALTER TABLE plantillas_aplicadas ADD COLUMN IF NOT EXISTS confirmada_en TIMESTAMP WITH TIME ZONE;
ALTER TABLE plantillas_aplicadas ADD COLUMN IF NOT EXISTS revertida_en TIMESTAMP WITH TIME ZONE;
-- huella de los parámetros (idempotencia ante doble clic) y espera entre reintentos de reversión
ALTER TABLE plantillas_aplicadas ADD COLUMN IF NOT EXISTS params_hash VARCHAR(64);
ALTER TABLE plantillas_aplicadas ADD COLUMN IF NOT EXISTS reintentar_en TIMESTAMP WITH TIME ZONE;

-- la tarea de reversión solo mira lo pendiente: índice parcial pequeño
CREATE INDEX IF NOT EXISTS idx_plantillas_pendientes
    ON plantillas_aplicadas (revertir_en)
    WHERE estado IN ('aplicada', 'aplicando');
