-- 007: VPN de personas (M3). Solo agrega columnas e índices; no cambia las existentes.
-- sede_id: sede a cuya LAN llega la persona. tenant_id: cartera (aliado) dueña de la sede.
-- preshared_key_enc: clave precompartida cifrada (VAULT_KEY). client_allowed: AllowedIPs del cliente.
-- tunnel_ip_historica: al revocar, tunnel_ip se libera (valor 'rev-<id>') para reutilizar el rango 200-250.
ALTER TABLE wg_peers ADD COLUMN IF NOT EXISTS sede_id INTEGER REFERENCES sedes(id) ON DELETE SET NULL;
ALTER TABLE wg_peers ADD COLUMN IF NOT EXISTS tenant_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL;
ALTER TABLE wg_peers ADD COLUMN IF NOT EXISTS preshared_key_enc TEXT;
ALTER TABLE wg_peers ADD COLUMN IF NOT EXISTS client_allowed TEXT;
ALTER TABLE wg_peers ADD COLUMN IF NOT EXISTS dns VARCHAR(45);
ALTER TABLE wg_peers ADD COLUMN IF NOT EXISTS tunnel_ip_historica VARCHAR(45);
CREATE INDEX IF NOT EXISTS idx_wg_peers_sede ON wg_peers(sede_id) WHERE tipo = 'usuario';
CREATE INDEX IF NOT EXISTS idx_wg_peers_tenant ON wg_peers(tenant_id) WHERE tipo = 'usuario';
