-- 001: esquema base de AVZCloud Central Manager (equivale a server/init_postgres.sql SIN semillas).
--
-- IDEMPOTENTE y segura de ejecutar sobre la base que YA existe en srv-2: solo usa
-- CREATE ... IF NOT EXISTS e INSERT ... ON CONFLICT DO NOTHING. No modifica ni borra datos,
-- no cambia columnas existentes y no crea usuarios, sedes ni dispositivos de demostracion.
-- Las filas de `roles` son catalogo del sistema (no son datos demo). El rol `tecnico` lo agrega
-- 005_rol_tecnico.sql (fuente unica; aqui no se repite para no duplicar la descripcion).
-- El runner (db.run_migrations) la ejecuta antes que 002 por orden alfabetico.

-- 1. Roles del Sistema
CREATE TABLE IF NOT EXISTS roles (
    id VARCHAR(30) PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL,
    descripcion TEXT
);

INSERT INTO roles (id, nombre, descripcion) VALUES
('superadmin', 'Super Administrador (AVZCloud)', 'Control total de la infraestructura, usuarios, roles, inventario y configuración SMTP.'),
('partner', 'Partner Comercial / Aliado', 'Gestión de clientes, centros comerciales y APs/equipos asignados a su cartera comercial.'),
('soporte', 'Soporte Técnico', 'Operación, diagnóstico, aprovisionamiento y adopción de equipos en terreno.'),
('cliente', 'Cliente Final / Centro Comercial', 'Modo exclusivamente de visualización de métricas de APs, clientes WiFi y enlaces.')
ON CONFLICT (id) DO NOTHING;

-- 2. Usuarios
CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    email VARCHAR(150) UNIQUE NOT NULL,
    username VARCHAR(80),
    nombre_completo VARCHAR(150) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    rol VARCHAR(30) NOT NULL REFERENCES roles(id) ON UPDATE CASCADE,
    partner_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    activo BOOLEAN DEFAULT TRUE,
    require_password_change BOOLEAN DEFAULT FALSE,
    ultimo_login TIMESTAMP WITH TIME ZONE,
    creado_en TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_usuarios_email ON usuarios(email);
CREATE INDEX IF NOT EXISTS idx_usuarios_partner ON usuarios(partner_id);

-- 3. Configuración SMTP
CREATE TABLE IF NOT EXISTS configuracion_smtp (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    smtp_host VARCHAR(150) NOT NULL,
    smtp_port INTEGER NOT NULL DEFAULT 587,
    smtp_user VARCHAR(150) NOT NULL,
    smtp_password VARCHAR(255) NOT NULL,
    smtp_encryption VARCHAR(15) NOT NULL DEFAULT 'TLS',
    remitente_email VARCHAR(150) NOT NULL,
    remitente_nombre VARCHAR(150) NOT NULL DEFAULT 'AVZCloud Alertas',
    actualizado_en TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 4. Recuperación de Clave
CREATE TABLE IF NOT EXISTS password_resets (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    token VARCHAR(64) UNIQUE NOT NULL,
    expira_en TIMESTAMP WITH TIME ZONE NOT NULL,
    utilizado BOOLEAN DEFAULT FALSE,
    creado_en TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. Auditoría
CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    usuario_email VARCHAR(150),
    accion VARCHAR(100) NOT NULL,
    detalles TEXT,
    ip_origen VARCHAR(45),
    fecha TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 6. Servicios Contratados
CREATE TABLE IF NOT EXISTS servicios_cliente (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    partner_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    nombre_servicio VARCHAR(150) NOT NULL,
    tipo VARCHAR(50) NOT NULL,
    ip_publica VARCHAR(45),
    vcores INTEGER,
    ram_gb INTEGER,
    disco_gb INTEGER,
    estado VARCHAR(30) DEFAULT 'Activo',
    sla_uptime NUMERIC(5,2) DEFAULT 99.98,
    actualizado_en TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 7. Sedes Físicas y Centros Comerciales
CREATE TABLE IF NOT EXISTS sedes (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(150) NOT NULL,
    ciudad VARCHAR(100) NOT NULL,
    direccion VARCHAR(255),
    cliente_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
    partner_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    contacto_nombre VARCHAR(150),
    contacto_telefono VARCHAR(50),
    contacto_email VARCHAR(150),
    estado VARCHAR(30) DEFAULT 'activa',
    creado_en TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sedes_cliente ON sedes(cliente_id);
CREATE INDEX IF NOT EXISTS idx_sedes_partner ON sedes(partner_id);

-- 8. Dispositivos de Red (Routers MikroTik, Switches y APs)
CREATE TABLE IF NOT EXISTS dispositivos (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(120) NOT NULL,
    tipo VARCHAR(50) NOT NULL DEFAULT 'access_point',
    marca VARCHAR(50) NOT NULL,
    modelo VARCHAR(80) NOT NULL,
    mac_address VARCHAR(30) UNIQUE,
    serial_number VARCHAR(80),
    sede_id INTEGER REFERENCES sedes(id) ON DELETE SET NULL,
    cliente_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    partner_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    ip_local VARCHAR(45),
    ip_publica VARCHAR(45),
    puerto_gestion INTEGER DEFAULT 8728,
    estado VARCHAR(30) DEFAULT 'online',
    ssid_principal VARCHAR(80),
    vlan_id INTEGER DEFAULT 10,
    clientes_conectados INTEGER DEFAULT 0,
    trafico_descarga_mbps NUMERIC(8,2) DEFAULT 0.0,
    trafico_subida_mbps NUMERIC(8,2) DEFAULT 0.0,
    uptime_horas INTEGER DEFAULT 0,
    detalles_config TEXT,
    comando_adopcion TEXT,
    ultimo_heartbeat TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    creado_en TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dispositivos_sede ON dispositivos(sede_id);
CREATE INDEX IF NOT EXISTS idx_dispositivos_cliente ON dispositivos(cliente_id);

-- 9. Access Points
CREATE TABLE IF NOT EXISTS access_points (
    id SERIAL PRIMARY KEY,
    nombre_ap VARCHAR(120) NOT NULL,
    marca VARCHAR(50) NOT NULL,
    modelo VARCHAR(80) NOT NULL,
    mac_address VARCHAR(30) UNIQUE NOT NULL,
    serial_number VARCHAR(80),
    ubicacion_sede VARCHAR(150) NOT NULL,
    sede_id INTEGER REFERENCES sedes(id) ON DELETE SET NULL,
    cliente_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    partner_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    ip_local VARCHAR(45),
    ip_publica VARCHAR(45),
    ssid_principal VARCHAR(80) DEFAULT 'AVZCloud-WiFi',
    vlan_id INTEGER DEFAULT 20,
    estado_adopcion VARCHAR(30) DEFAULT 'adoptado',
    clientes_conectados INTEGER DEFAULT 0,
    trafico_descarga_mbps NUMERIC(8,2) DEFAULT 0.0,
    trafico_subida_mbps NUMERIC(8,2) DEFAULT 0.0,
    inform_url VARCHAR(255),
    comando_adopcion TEXT,
    fecha_adopcion TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    ultimo_heartbeat TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
