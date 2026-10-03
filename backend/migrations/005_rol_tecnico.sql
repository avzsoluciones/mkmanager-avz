-- 005: rol "tecnico" = técnico de un aliado. Comparte la cartera (tenant) de su aliado y solo opera dentro de ella.
INSERT INTO roles (id, nombre, descripcion) VALUES
('tecnico', 'Técnico del aliado', 'Opera y da soporte únicamente a las sedes y equipos de la cartera de su aliado.')
ON CONFLICT (id) DO NOTHING;
