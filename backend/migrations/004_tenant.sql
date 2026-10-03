-- 004: inquilino (tenant) para que varios usuarios de un aliado compartan la misma cartera.
-- tenant_id apunta a la cuenta principal del aliado. Los demás roles lo dejan nulo.
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS tenant_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL;
UPDATE usuarios SET tenant_id = id WHERE rol = 'partner' AND tenant_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_usuarios_tenant ON usuarios(tenant_id);
