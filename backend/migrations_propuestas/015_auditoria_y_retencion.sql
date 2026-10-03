-- BORRADOR SIN PROBAR (arquitecto de datos, 2026-10-03). NO se aplica: el ejecutor solo lee backend/migrations.
-- 015: auditoria e historial.
ALTER TABLE audit_logs ALTER COLUMN usuario_email TYPE VARCHAR(320);
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS usuario_id INTEGER;     -- instantanea, SIN FK a proposito
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS aliado_id INTEGER;      -- instantanea del alcance, SIN FK
ALTER TABLE audit_logs ALTER COLUMN fecha SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_fecha ON audit_logs (fecha);
CREATE INDEX IF NOT EXISTS idx_audit_email ON audit_logs (lower(usuario_email), id DESC);
CREATE INDEX IF NOT EXISTS idx_audit_aliado ON audit_logs (aliado_id, id DESC) WHERE aliado_id IS NOT NULL;

CREATE OR REPLACE FUNCTION audit_logs_solo_anexar() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit_logs es de solo anexado'; END $$;
DROP TRIGGER IF EXISTS trg_audit_no_update ON audit_logs;
CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON audit_logs FOR EACH ROW EXECUTE FUNCTION audit_logs_solo_anexar();

CREATE INDEX IF NOT EXISTS idx_alertas_cerrado ON alertas (cerrado_en) WHERE estado = 'cerrado';   -- retencion
ALTER TABLE dispositivos SET (fillfactor = 85);          -- actualizaciones HOT del monitoreo (monitoring.py:276)
ALTER TABLE metricas_equipo SET (autovacuum_vacuum_scale_factor = 0.02);

-- Retencion (a ejecutar desde la tarea horaria, junto a metricas_equipo):
--   DELETE FROM audit_logs WHERE fecha < now() - interval '13 months';
--   DELETE FROM alertas WHERE estado = 'cerrado' AND cerrado_en < now() - interval '24 months';
--   DELETE FROM password_resets WHERE expira_en < now() - interval '7 days';
