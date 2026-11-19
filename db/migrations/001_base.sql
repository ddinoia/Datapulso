-- DataPulso · migración 001 · esquema base multi-tenant del hito 1
-- Convención: todas las tablas de negocio llevan tenant_id y RLS (aislamiento por fila).
-- La aplicación se conecta con el rol `datapulso_app` (sin BYPASSRLS, no dueño de las tablas)
-- y fija el tenant por transacción con: SELECT set_config('app.tenant_id', '<uuid>', true)

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE FUNCTION app_tenant() RETURNS uuid
  LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$;

CREATE TABLE tenants (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre                text NOT NULL UNIQUE,
  industria             text NOT NULL,
  idioma                text NOT NULL DEFAULT 'es',
  moneda                text NOT NULL DEFAULT 'ARS',
  zona_horaria          text NOT NULL DEFAULT 'America/Argentina/Buenos_Aires',
  config_version_activa integer,
  is_demo               boolean NOT NULL DEFAULT false,
  creado_en             timestamptz NOT NULL DEFAULT now()
);

-- Versiones de configuración: inmutables. Cambiar algo = crear otra versión.
CREATE TABLE config_versions (
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  version    integer NOT NULL,
  contenido  jsonb NOT NULL,
  creado_por text NOT NULL,
  nota       text,
  creado_en  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, version)
);

CREATE FUNCTION bloquear_modificacion() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'La tabla % es inmutable: cree una versión nueva', TG_TABLE_NAME;
END $$;
CREATE TRIGGER config_versions_inmutable BEFORE UPDATE OR DELETE ON config_versions
  FOR EACH ROW EXECUTE FUNCTION bloquear_modificacion();

CREATE TABLE fuentes (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  nombre             text NOT NULL,
  tipo               text NOT NULL CHECK (tipo IN ('csv','base_de_datos','api')),
  destino            text NOT NULL CHECK (destino IN ('entidades','eventos','pagos')),
  mapeo              jsonb NOT NULL,
  parametros         jsonb NOT NULL DEFAULT '{}',   -- sin secretos
  credencial_cifrada bytea,                          -- cifrada; en producción, referencia al gestor de secretos
  frecuencia         text,                           -- expresión cron o 'manual'
  marca_de_agua      text,                           -- última marca sincronizada (fuentes incrementales)
  estado             text NOT NULL DEFAULT 'activa',
  is_demo            boolean NOT NULL DEFAULT false,
  creado_en          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, nombre)
);

CREATE TABLE cargas (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  fuente_id           uuid NOT NULL REFERENCES fuentes(id) ON DELETE CASCADE,
  origen              text,                          -- nombre de archivo o descripción de la consulta
  estado              text NOT NULL DEFAULT 'en_curso' CHECK (estado IN ('en_curso','ok','error')),
  inicio              timestamptz NOT NULL DEFAULT now(),
  fin                 timestamptz,
  filas_leidas        integer NOT NULL DEFAULT 0,
  filas_aceptadas     integer NOT NULL DEFAULT 0,
  filas_rechazadas    integer NOT NULL DEFAULT 0,
  filas_insertadas    integer NOT NULL DEFAULT 0,
  filas_actualizadas  integer NOT NULL DEFAULT 0,
  marca_antes         text,
  marca_despues       text,
  reporte_calidad     jsonb,
  error               text,
  is_demo             boolean NOT NULL DEFAULT false
);

-- Staging: filas crudas de la carga en curso. Las aceptadas se borran al cerrar la carga;
-- las rechazadas quedan con su motivo para poder auditarlas.
CREATE TABLE stg_filas (
  carga_id   uuid NOT NULL REFERENCES cargas(id) ON DELETE CASCADE,
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  nro_fila   integer NOT NULL,
  crudo      jsonb NOT NULL,
  estado     text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente','aceptada','rechazada')),
  motivo     text,
  PRIMARY KEY (carga_id, nro_fila)
);

CREATE TABLE entidades (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  id_externo     text NOT NULL,
  tipo           text NOT NULL DEFAULT 'persona' CHECK (tipo IN ('persona','empresa')),
  parent_id      uuid,                               -- jerarquía empresa -> usuarios (B2B2C)
  fecha_alta     date NOT NULL,
  fecha_baja     date,
  estado_externo text,
  atributos      jsonb NOT NULL DEFAULT '{}',
  is_demo        boolean NOT NULL DEFAULT false,
  carga_id       uuid,
  creado_en      timestamptz NOT NULL DEFAULT now(),
  actualizado_en timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id_externo),
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, parent_id) REFERENCES entidades (tenant_id, id)
);
CREATE INDEX entidades_parent_idx ON entidades (tenant_id, parent_id);

CREATE TABLE eventos (
  id         bigserial PRIMARY KEY,
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  entidad_id uuid NOT NULL,
  clave      text NOT NULL,        -- id_externo del evento o hash determinístico (idempotencia)
  id_externo text,
  ts         timestamptz NOT NULL,
  tipo       text NOT NULL,
  monto      numeric(18,2),
  moneda     text,
  canal      text,
  categoria  text,
  atributos  jsonb NOT NULL DEFAULT '{}',
  is_demo    boolean NOT NULL DEFAULT false,
  carga_id   uuid,
  UNIQUE (tenant_id, clave),
  FOREIGN KEY (tenant_id, entidad_id) REFERENCES entidades (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX eventos_entidad_ts_idx ON eventos (tenant_id, entidad_id, ts);
CREATE INDEX eventos_tipo_ts_idx ON eventos (tenant_id, tipo, ts);

CREATE TABLE pagos (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  entidad_id        uuid NOT NULL,
  comprobante       text NOT NULL,
  fecha_emision     date NOT NULL,
  fecha_vencimiento date NOT NULL,
  monto             numeric(18,2) NOT NULL,
  moneda            text,
  fecha_pago        date,
  monto_pagado      numeric(18,2),
  atributos         jsonb NOT NULL DEFAULT '{}',
  is_demo           boolean NOT NULL DEFAULT false,
  carga_id          uuid,
  UNIQUE (tenant_id, comprobante),
  FOREIGN KEY (tenant_id, entidad_id) REFERENCES entidades (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX pagos_entidad_idx ON pagos (tenant_id, entidad_id);
CREATE INDEX pagos_venc_idx ON pagos (tenant_id, fecha_vencimiento);

CREATE TABLE auditoria (
  id        bigserial PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  ts        timestamptz NOT NULL DEFAULT now(),
  actor     text NOT NULL,
  accion    text NOT NULL,
  detalle   jsonb NOT NULL DEFAULT '{}'
);
CREATE TRIGGER auditoria_inmutable BEFORE UPDATE OR DELETE ON auditoria
  FOR EACH ROW EXECUTE FUNCTION bloquear_modificacion();

-- Aislamiento por fila
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['config_versions','fuentes','cargas','stg_filas','entidades',
                           'eventos','pagos','auditoria'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY aislamiento ON %I USING (tenant_id = app_tenant()) WITH CHECK (tenant_id = app_tenant())', t);
  END LOOP;
END $$;
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
CREATE POLICY aislamiento ON tenants USING (id = app_tenant()) WITH CHECK (id = app_tenant());

-- Permisos del rol de la aplicación (el rol lo crea el migrador antes de este script)
GRANT USAGE ON SCHEMA public TO datapulso_app;
GRANT SELECT, UPDATE ON tenants TO datapulso_app;
GRANT SELECT, INSERT ON config_versions, auditoria TO datapulso_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON fuentes, cargas, stg_filas, entidades, eventos, pagos TO datapulso_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO datapulso_app;
GRANT EXECUTE ON FUNCTION app_tenant() TO datapulso_app;
