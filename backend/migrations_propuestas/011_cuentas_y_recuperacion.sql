-- BORRADOR SIN PROBAR (arquitecto de datos, 2026-10-03). NO se aplica: el ejecutor solo lee backend/migrations.
-- Depende de 010 (usa el helper _avz_add_constraint).
-- 011: cuentas. Idempotente.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM usuarios GROUP BY lower(btrim(email)) HAVING count(*) > 1) THEN
    RAISE EXCEPTION '011: correos duplicados ignorando mayusculas.';
  END IF;
  IF EXISTS (SELECT 1 FROM usuarios WHERE nullif(btrim(username), '') IS NOT NULL
             GROUP BY lower(btrim(username)) HAVING count(*) > 1) THEN
    RAISE EXCEPTION '011: nombres de usuario duplicados.';
  END IF;
  IF EXISTS (SELECT 1 FROM usuarios WHERE username LIKE '%@%') THEN
    RAISE EXCEPTION '011: hay nombres de usuario con @; renombrelos.';
  END IF;
END $$;

UPDATE usuarios SET email = lower(btrim(email)) WHERE email <> lower(btrim(email));
UPDATE usuarios SET username = nullif(lower(btrim(username)), '')
 WHERE username IS DISTINCT FROM nullif(lower(btrim(username)), '');
UPDATE usuarios SET activo = TRUE WHERE activo IS NULL;
UPDATE usuarios SET require_password_change = FALSE WHERE require_password_change IS NULL;
UPDATE usuarios SET creado_en = now() WHERE creado_en IS NULL;
ALTER TABLE usuarios ALTER COLUMN activo SET NOT NULL;
ALTER TABLE usuarios ALTER COLUMN require_password_change SET NOT NULL;
ALTER TABLE usuarios ALTER COLUMN creado_en SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_usuarios_email_lower ON usuarios (lower(email));
CREATE UNIQUE INDEX IF NOT EXISTS uq_usuarios_username_lower ON usuarios (lower(username)) WHERE username IS NOT NULL;
DROP INDEX IF EXISTS idx_usuarios_email;
SELECT _avz_add_constraint('usuarios', 'ck_usuarios_username', $c$CHECK (username IS NULL OR username !~ '@')$c$);
SELECT _avz_add_constraint('usuarios', 'ck_usuarios_email', $c$CHECK (email ~ '^[^@[:space:]]{1,64}@[^@[:space:]]+\.[^@[:space:]]+$')$c$);

ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS password_cambiada_en TIMESTAMPTZ;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now();

-- password_resets: solo el hash del token. Los tokens que hubiera en claro se invalidan.
ALTER TABLE password_resets ADD COLUMN IF NOT EXISTS token_hash CHAR(64);
ALTER TABLE password_resets ADD COLUMN IF NOT EXISTS usado_en TIMESTAMPTZ;
ALTER TABLE password_resets ADD COLUMN IF NOT EXISTS solicitado_ip VARCHAR(45);
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema = current_schema() AND table_name = 'password_resets' AND column_name = 'token') THEN
    EXECUTE $q$UPDATE password_resets
                SET token_hash = encode(sha256(convert_to(token, 'UTF8')), 'hex'),
                    utilizado = TRUE, usado_en = COALESCE(usado_en, now())
                WHERE token_hash IS NULL$q$;
    EXECUTE 'ALTER TABLE password_resets DROP COLUMN token';
  END IF;
END $$;
UPDATE password_resets SET utilizado = FALSE WHERE utilizado IS NULL;
ALTER TABLE password_resets ALTER COLUMN utilizado SET NOT NULL;
ALTER TABLE password_resets ALTER COLUMN utilizado SET DEFAULT FALSE;
ALTER TABLE password_resets ALTER COLUMN token_hash SET NOT NULL;
ALTER TABLE password_resets ALTER COLUMN creado_en SET NOT NULL;
SELECT _avz_add_constraint('password_resets', 'ck_reset_hash', $c$CHECK (token_hash ~ '^[0-9a-f]{64}$')$c$);
SELECT _avz_add_constraint('password_resets', 'ck_reset_vigencia', $c$CHECK (expira_en > creado_en)$c$);
CREATE UNIQUE INDEX IF NOT EXISTS uq_password_resets_hash ON password_resets (token_hash);
CREATE INDEX IF NOT EXISTS idx_password_resets_usuario ON password_resets (usuario_id) WHERE NOT utilizado;
CREATE INDEX IF NOT EXISTS idx_password_resets_expira ON password_resets (expira_en);   -- limpieza
