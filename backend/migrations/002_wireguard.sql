-- 002: túneles WireGuard y plantillas aplicadas. Migración aditiva: no modifica datos existentes.

ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS tunnel_ip VARCHAR(45);
ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS lan_cidr VARCHAR(45);
CREATE UNIQUE INDEX IF NOT EXISTS ux_dispositivos_tunnel_ip
    ON dispositivos(tunnel_ip) WHERE tunnel_ip IS NOT NULL;

CREATE TABLE IF NOT EXISTS wg_peers (
    id SERIAL PRIMARY KEY,
    tipo VARCHAR(10) NOT NULL CHECK (tipo IN ('router', 'usuario')),
    nombre VARCHAR(120) NOT NULL,
    dispositivo_id INTEGER REFERENCES dispositivos(id) ON DELETE CASCADE,
    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    tunnel_ip VARCHAR(45) NOT NULL UNIQUE,
    public_key VARCHAR(64) NOT NULL UNIQUE,
    private_key_enc TEXT NOT NULL,
    hub_peer_id VARCHAR(20),
    allowed_extra TEXT,
    creado_por VARCHAR(150),
    creado_en TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    revocado_en TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS plantillas_aplicadas (
    id SERIAL PRIMARY KEY,
    dispositivo_id INTEGER REFERENCES dispositivos(id) ON DELETE CASCADE,
    plantilla VARCHAR(80) NOT NULL,
    parametros JSONB NOT NULL DEFAULT '{}'::jsonb,
    script TEXT,
    modo VARCHAR(20) NOT NULL DEFAULT 'push',
    estado VARCHAR(20) NOT NULL DEFAULT 'pendiente',
    resultado TEXT,
    revertir_en TIMESTAMP WITH TIME ZONE,
    usuario_email VARCHAR(150),
    creado_en TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_plantillas_disp ON plantillas_aplicadas(dispositivo_id);
