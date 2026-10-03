-- 008: monitoreo de túneles y equipos, historial de métricas y alertas por correo. Migración aditiva.

ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS estado_desde TIMESTAMP WITH TIME ZONE;
-- Segunda señal de vida: último rx del peer, cuándo se tomó y cuándo se vio crecer por última vez.
ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS rx_ultimo BIGINT;
ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS rx_muestra_en TIMESTAMP WITH TIME ZONE;
ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS rx_activo_en TIMESTAMP WITH TIME ZONE;

CREATE TABLE IF NOT EXISTS metricas_equipo (
    id BIGSERIAL PRIMARY KEY,
    equipo_id INTEGER NOT NULL REFERENCES dispositivos(id) ON DELETE CASCADE,
    instante TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    estado VARCHAR(20) NOT NULL CHECK (estado IN ('online', 'offline', 'pendiente')),
    rx_bytes BIGINT,
    tx_bytes BIGINT
);
CREATE INDEX IF NOT EXISTS idx_metricas_equipo_instante ON metricas_equipo(equipo_id, instante DESC);
CREATE INDEX IF NOT EXISTS idx_metricas_instante ON metricas_equipo(instante);   -- limpieza por retención

-- Un incidente = una fila. Solo puede haber un incidente abierto por equipo.
CREATE TABLE IF NOT EXISTS alertas (
    id SERIAL PRIMARY KEY,
    dispositivo_id INTEGER NOT NULL REFERENCES dispositivos(id) ON DELETE CASCADE,
    estado VARCHAR(10) NOT NULL DEFAULT 'abierto' CHECK (estado IN ('abierto', 'cerrado')),
    inicio TIMESTAMP WITH TIME ZONE NOT NULL,
    cerrado_en TIMESTAMP WITH TIME ZONE,
    notificado_caida_en TIMESTAMP WITH TIME ZONE,
    notificado_recuperacion_en TIMESTAMP WITH TIME ZONE,
    intentos_caida INTEGER NOT NULL DEFAULT 0,
    intentos_recuperacion INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_alertas_abierta ON alertas(dispositivo_id) WHERE estado = 'abierto';
CREATE INDEX IF NOT EXISTS idx_alertas_dispositivo ON alertas(dispositivo_id, inicio DESC);
