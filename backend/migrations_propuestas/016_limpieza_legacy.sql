-- BORRADOR SIN PROBAR (arquitecto de datos, 2026-10-03). NO se aplica: el ejecutor solo lee backend/migrations.
-- RIESGO ALTO E IRREVERSIBLE: solo despues del piloto, con pg_dump completo previo, y cuando el codigo ya no lea
-- las columnas viejas. Depende de 012 (cifrado SMTP via CLI) y 013 (aliados).
-- 016: elimina lo heredado. Las tablas con datos NO se tocan (se avisa).
DO $$
DECLARE t text; n bigint;
BEGIN
  FOREACH t IN ARRAY ARRAY['access_points', 'servicios_cliente'] LOOP
    IF to_regclass(t) IS NOT NULL THEN
      EXECUTE format('SELECT count(*) FROM %I', t) INTO n;
      IF n = 0 THEN EXECUTE format('DROP TABLE %I', t);
      ELSE RAISE NOTICE '016: % tiene % filas; exportar y eliminar a mano', t, n; END IF;
    END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema = current_schema() AND table_name = 'configuracion_smtp'
               AND column_name = 'smtp_password') THEN
    IF EXISTS (SELECT 1 FROM configuracion_smtp WHERE smtp_password IS NOT NULL) THEN
      RAISE NOTICE '016: smtp_password aun tiene datos; ejecutar app.cli smtp-cifrar primero';
    ELSE
      ALTER TABLE configuracion_smtp DROP COLUMN smtp_password;
    END IF;
  END IF;
END $$;

ALTER TABLE dispositivos
  DROP COLUMN IF EXISTS ssid_principal, DROP COLUMN IF EXISTS vlan_id, DROP COLUMN IF EXISTS clientes_conectados,
  DROP COLUMN IF EXISTS trafico_descarga_mbps, DROP COLUMN IF EXISTS trafico_subida_mbps,
  DROP COLUMN IF EXISTS uptime_horas, DROP COLUMN IF EXISTS cliente_id, DROP COLUMN IF EXISTS partner_id,
  DROP COLUMN IF EXISTS ip_publica, DROP COLUMN IF EXISTS comando_adopcion, DROP COLUMN IF EXISTS detalles_config;
ALTER TABLE sedes DROP COLUMN IF EXISTS cliente_id, DROP COLUMN IF EXISTS partner_id;
ALTER TABLE empresas DROP COLUMN IF EXISTS partner_id;
ALTER TABLE usuarios DROP COLUMN IF EXISTS partner_id, DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE wg_peers DROP COLUMN IF EXISTS tenant_id, DROP COLUMN IF EXISTS usuario_id;
