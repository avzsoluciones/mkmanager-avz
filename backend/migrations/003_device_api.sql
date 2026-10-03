-- 003: credenciales de API por dispositivo (usuario dedicado, contraseña aleatoria cifrada).
ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS api_user VARCHAR(60);
ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS api_password_enc TEXT;
ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS wan_config JSONB;
ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS lan_config JSONB;
