-- Jerarquía obligatoria Aliado -> Empresa -> Sede -> Equipo (spec 001, M8).
CREATE TABLE IF NOT EXISTS empresas (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(150) NOT NULL,
    partner_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    contacto_email VARCHAR(150),
    creado_en TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
-- Nombre único por aliado, sin distinguir mayúsculas.
CREATE UNIQUE INDEX IF NOT EXISTS uq_empresas_nombre ON empresas (LOWER(nombre), COALESCE(partner_id, 0));
CREATE INDEX IF NOT EXISTS idx_empresas_partner ON empresas(partner_id);

ALTER TABLE sedes ADD COLUMN IF NOT EXISTS empresa_id INTEGER REFERENCES empresas(id) ON DELETE RESTRICT;

-- Las sedes anteriores pasan a una empresa «General» por aliado.
INSERT INTO empresas (nombre, partner_id)
    SELECT DISTINCT 'General', partner_id FROM sedes WHERE empresa_id IS NULL
    ON CONFLICT DO NOTHING;
UPDATE sedes s SET empresa_id = e.id
    FROM empresas e
    WHERE s.empresa_id IS NULL AND LOWER(e.nombre) = 'general' AND e.partner_id IS NOT DISTINCT FROM s.partner_id;

ALTER TABLE sedes ALTER COLUMN empresa_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sedes_empresa ON sedes(empresa_id);
