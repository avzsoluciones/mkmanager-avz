-- BORRADOR SIN PROBAR (arquitecto de datos, 2026-10-03). NO se aplica: el ejecutor solo lee backend/migrations.
-- Depende de 010 (usa el helper _avz_add_constraint).
-- 012: SMTP con contrasena cifrada (VAULT_KEY). El SQL no puede cifrar: lo hace app.cli (paso 2).
ALTER TABLE configuracion_smtp ADD COLUMN IF NOT EXISTS smtp_password_enc TEXT;
ALTER TABLE configuracion_smtp ALTER COLUMN smtp_password DROP NOT NULL;
ALTER TABLE configuracion_smtp ALTER COLUMN smtp_user DROP NOT NULL;      -- reles sin autenticacion
ALTER TABLE configuracion_smtp ADD COLUMN IF NOT EXISTS habilitado BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE configuracion_smtp ADD COLUMN IF NOT EXISTS actualizado_por VARCHAR(150);
ALTER TABLE configuracion_smtp ADD COLUMN IF NOT EXISTS ultimo_envio_en TIMESTAMPTZ;
ALTER TABLE configuracion_smtp ADD COLUMN IF NOT EXISTS ultimo_error VARCHAR(300);

UPDATE configuracion_smtp SET smtp_encryption = CASE WHEN upper(smtp_encryption) = 'SSL' THEN 'SSL' ELSE 'TLS' END
 WHERE smtp_encryption NOT IN ('TLS','SSL');
SELECT _avz_add_constraint('configuracion_smtp', 'ck_smtp_encryption', $c$CHECK (smtp_encryption IN ('TLS','SSL'))$c$);
SELECT _avz_add_constraint('configuracion_smtp', 'ck_smtp_port', $c$CHECK (smtp_port BETWEEN 1 AND 65535)$c$);
-- Paso 2 (fuera del SQL): `python -m app.cli smtp-cifrar` (a crear) cifra smtp_password -> smtp_password_enc
--   y deja smtp_password = NULL. monitoring.py:307 y alerts.py:72 deben leer y descifrar smtp_password_enc.
-- Paso 3: la 016 elimina la columna smtp_password solo si ya es NULL.
