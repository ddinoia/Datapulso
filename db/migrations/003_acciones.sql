-- DataPulso · migración 003 · acciones, corridas y salidas (hito 3)
-- Todo con tenant_id y RLS. La inmutabilidad después de aprobar se impone en la base (triggers), no solo en el código:
-- ni el rol de la app ni el rol dueño pueden cambiar la proyección, la lista o los grupos de una corrida aprobada.

-- Usuarios por tenant (sin login hasta el hito 5: el comando recibe --usuario y todo queda en auditoría)
CREATE TABLE usuarios (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  nombre     text NOT NULL,
  rol        text NOT NULL CHECK (rol IN ('administrador', 'analista', 'aprobador', 'solo_lectura')),
  activo     boolean NOT NULL DEFAULT true,
  is_demo    boolean NOT NULL DEFAULT false,
  creado_en  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, nombre),
  UNIQUE (tenant_id, id)
);

-- Catálogo de acciones: versiones inmutables (cambiar una acción = insertar la versión siguiente del mismo código)
CREATE TABLE acciones_catalogo (
  id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id                 uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  codigo                    text NOT NULL,
  version                   integer NOT NULL,
  nombre                    text NOT NULL,
  etapa                     text NOT NULL CHECK (etapa IN ('retencion', 'cobranza', 'activacion')),
  tipo                      text NOT NULL CHECK (tipo IN ('mensaje', 'oferta_beneficio', 'tarea_humana', 'lista_llamadas', 'webhook')),
  canal                     text NOT NULL,
  plantilla                 text,
  costo_unitario            numeric(14,2) NOT NULL CHECK (costo_unitario >= 0),
  elegibilidad              jsonb NOT NULL DEFAULT '{}',   -- {"bandas": [...], "segmentos": [...]} (vacío = todos)
  frecuencia_max_dias       integer NOT NULL DEFAULT 30 CHECK (frecuencia_max_dias >= 0),
  requiere_aprobacion       boolean NOT NULL DEFAULT true CHECK (requiere_aprobacion),  -- decisión de Diego: todas, al inicio
  modifica_sistema_cliente  boolean NOT NULL DEFAULT false,  -- bloquear, otorgar crédito, ajustar plan: nunca solo
  impacto_esperado          numeric(5,4) NOT NULL CHECK (impacto_esperado >= 0 AND impacto_esperado <= 1),
  impacto_origen            text NOT NULL DEFAULT 'supuesto' CHECK (impacto_origen IN ('supuesto', 'medido')),
  activa                    boolean NOT NULL DEFAULT true,
  is_demo                   boolean NOT NULL DEFAULT false,
  creado_por                text NOT NULL,
  creado_en                 timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, codigo, version),
  UNIQUE (tenant_id, id)
);
CREATE TRIGGER acciones_catalogo_inmutable BEFORE UPDATE OR DELETE ON acciones_catalogo
  FOR EACH ROW EXECUTE FUNCTION bloquear_modificacion();

CREATE TABLE corridas (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  numero              integer NOT NULL,
  accion_id           uuid NOT NULL,
  etapa               text NOT NULL,
  estado              text NOT NULL DEFAULT 'borrador' CHECK (estado IN ('borrador', 'pendiente_aprobacion', 'aprobada',
                        'lanzada', 'medicion_en_curso', 'cerrada', 'cancelada')),
  creada_por          uuid NOT NULL,
  aprobada_por        uuid,
  aprobada_en         timestamptz,
  cancelada_por       uuid,
  motivo_cancelacion  text,
  lanzada_por         uuid,
  fecha_lanzamiento   date,                  -- fecha real en que el cliente ejecutó la acción (D8)
  fecha_puntajes      date NOT NULL,
  modelo_version      text NOT NULL,
  config_version      integer NOT NULL,      -- configuración vigente al crear (la de la aprobación va en la proyección)
  pct_control         numeric(5,2) NOT NULL CHECK (pct_control > 0 AND pct_control < 50),
  semilla             bigint NOT NULL,
  filtros             jsonb NOT NULL DEFAULT '{}',
  exclusiones         jsonb NOT NULL DEFAULT '{}',   -- cuántos quedaron afuera, por motivo (D6)
  diseno              jsonb NOT NULL DEFAULT '{}',   -- tamaños, estratos, efecto mínimo detectable (D5)
  proyeccion          jsonb,                         -- se congela al aprobar (D2)
  is_demo             boolean NOT NULL DEFAULT false,
  creado_en           timestamptz NOT NULL DEFAULT now(),
  actualizado_en      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, numero),
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, accion_id) REFERENCES acciones_catalogo (tenant_id, id),
  FOREIGN KEY (tenant_id, creada_por) REFERENCES usuarios (tenant_id, id),
  FOREIGN KEY (tenant_id, aprobada_por) REFERENCES usuarios (tenant_id, id),
  FOREIGN KEY (tenant_id, cancelada_por) REFERENCES usuarios (tenant_id, id),
  FOREIGN KEY (tenant_id, lanzada_por) REFERENCES usuarios (tenant_id, id),
  CHECK (estado NOT IN ('aprobada', 'lanzada', 'medicion_en_curso', 'cerrada')
         OR (proyeccion IS NOT NULL AND aprobada_por IS NOT NULL AND aprobada_en IS NOT NULL)),
  CHECK (estado <> 'cancelada' OR (cancelada_por IS NOT NULL AND coalesce(motivo_cancelacion, '') <> '')),
  CHECK (estado NOT IN ('lanzada', 'medicion_en_curso', 'cerrada') OR (fecha_lanzamiento IS NOT NULL AND lanzada_por IS NOT NULL))
);
CREATE INDEX corridas_estado_idx ON corridas (tenant_id, etapa, estado);

CREATE TABLE corrida_entidades (
  corrida_id      uuid NOT NULL,
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  entidad_id      uuid NOT NULL,
  etapa           text NOT NULL,         -- copia de la etapa de la corrida (para el control de corridas abiertas)
  grupo           text NOT NULL CHECK (grupo IN ('tratamiento', 'control')),
  banda           text NOT NULL,
  segmento        text NOT NULL,
  puntaje         numeric(6,5) NOT NULL,  -- probabilidad calibrada usada en la proyección
  motivo          text,                   -- driver principal del puntaje, para la exportación
  contactado_ts   timestamptz,            -- lo completa el hito 4 si el cliente informa el contacto
  estado          text NOT NULL DEFAULT 'pendiente',
  is_demo         boolean NOT NULL DEFAULT false,
  PRIMARY KEY (corrida_id, entidad_id),
  FOREIGN KEY (tenant_id, corrida_id) REFERENCES corridas (tenant_id, id),
  FOREIGN KEY (tenant_id, entidad_id) REFERENCES entidades (tenant_id, id)
);
CREATE INDEX corrida_entidades_entidad_idx ON corrida_entidades (tenant_id, entidad_id, etapa);

CREATE TABLE corrida_excluidos (
  corrida_id  uuid NOT NULL,
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  entidad_id  uuid NOT NULL,
  motivo      text NOT NULL,
  is_demo     boolean NOT NULL DEFAULT false,
  PRIMARY KEY (corrida_id, entidad_id),
  FOREIGN KEY (tenant_id, corrida_id) REFERENCES corridas (tenant_id, id)
);

-- Salidas habilitadas por el tenant (webhook o tareas humanas en su sistema). Sin salida habilitada no se emite nada.
CREATE TABLE salidas (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  nombre           text NOT NULL,
  url              text NOT NULL,
  secreto_cifrado  bytea NOT NULL,          -- clave HMAC, cifrada como las credenciales de fuentes
  tipos            text[] NOT NULL DEFAULT ARRAY['webhook', 'tarea_humana'],
  habilitada       boolean NOT NULL DEFAULT true,
  is_demo          boolean NOT NULL DEFAULT false,
  creado_en        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, nombre),
  UNIQUE (tenant_id, id)
);

-- Registro de entregas: una fila por intento (solo inserción)
CREATE TABLE entregas_webhook (
  id                  bigserial PRIMARY KEY,
  tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  corrida_id          uuid NOT NULL,
  salida_id           uuid NOT NULL,
  lote                integer NOT NULL,
  clave_idempotencia  text NOT NULL,
  intento             integer NOT NULL,
  estado              text NOT NULL CHECK (estado IN ('entregada', 'fallida')),
  codigo_http         integer,
  error               text,
  hash_cuerpo         text NOT NULL,
  is_demo             boolean NOT NULL DEFAULT false,
  ts                  timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, corrida_id) REFERENCES corridas (tenant_id, id),
  FOREIGN KEY (tenant_id, salida_id) REFERENCES salidas (tenant_id, id)
);
CREATE INDEX entregas_clave_idx ON entregas_webhook (tenant_id, clave_idempotencia, estado);
CREATE TRIGGER entregas_webhook_inmutable BEFORE UPDATE OR DELETE ON entregas_webhook
  FOR EACH ROW EXECUTE FUNCTION bloquear_modificacion();

-- ---------------------------------------------------------------- reglas de corridas en la base
-- Transiciones válidas (sección 7 de PRODUCTO.md) e inmutabilidad desde la aprobación.
CREATE FUNCTION corridas_proteger() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  permitidas jsonb := '{"borrador": ["borrador", "pendiente_aprobacion", "cancelada"],
                        "pendiente_aprobacion": ["pendiente_aprobacion", "borrador", "aprobada", "cancelada"],
                        "aprobada": ["aprobada", "lanzada", "cancelada"],
                        "lanzada": ["lanzada", "medicion_en_curso"],
                        "medicion_en_curso": ["medicion_en_curso", "cerrada"],
                        "cerrada": ["cerrada"], "cancelada": ["cancelada"]}';
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Las corridas no se borran: se cancelan con motivo';
  END IF;
  IF NOT (permitidas -> OLD.estado) ? NEW.estado THEN
    RAISE EXCEPTION 'Transición de estado no permitida: % -> %', OLD.estado, NEW.estado;
  END IF;
  IF OLD.estado NOT IN ('borrador', 'pendiente_aprobacion') AND
     (NEW.id, NEW.tenant_id, NEW.numero, NEW.accion_id, NEW.etapa, NEW.creada_por, NEW.aprobada_por, NEW.aprobada_en,
      NEW.fecha_puntajes, NEW.modelo_version, NEW.config_version, NEW.pct_control, NEW.semilla, NEW.filtros,
      NEW.exclusiones, NEW.diseno, NEW.proyeccion, NEW.is_demo, NEW.creado_en)
     IS DISTINCT FROM
     (OLD.id, OLD.tenant_id, OLD.numero, OLD.accion_id, OLD.etapa, OLD.creada_por, OLD.aprobada_por, OLD.aprobada_en,
      OLD.fecha_puntajes, OLD.modelo_version, OLD.config_version, OLD.pct_control, OLD.semilla, OLD.filtros,
      OLD.exclusiones, OLD.diseno, OLD.proyeccion, OLD.is_demo, OLD.creado_en) THEN
    RAISE EXCEPTION 'La corrida % ya fue aprobada: la proyección, la lista y el diseño no se pueden modificar', OLD.numero;
  END IF;
  IF OLD.estado IN ('lanzada', 'medicion_en_curso', 'cerrada') AND
     (NEW.fecha_lanzamiento, NEW.lanzada_por) IS DISTINCT FROM (OLD.fecha_lanzamiento, OLD.lanzada_por) THEN
    RAISE EXCEPTION 'La fecha de lanzamiento de la corrida % ya quedó registrada', OLD.numero;
  END IF;
  NEW.actualizado_en := now();
  RETURN NEW;
END $$;
CREATE TRIGGER corridas_proteger BEFORE UPDATE OR DELETE ON corridas
  FOR EACH ROW EXECUTE FUNCTION corridas_proteger();

-- La lista y los grupos solo se escriben mientras la corrida está en borrador. Después, solo el seguimiento del
-- contacto (contactado_ts, estado) que usa el hito 4. Un cliente no puede estar en dos corridas abiertas de la
-- misma etapa (D4).
CREATE FUNCTION corrida_entidades_proteger() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  est text;
  otra integer;
BEGIN
  SELECT estado INTO est FROM corridas WHERE id = COALESCE(NEW.corrida_id, OLD.corrida_id);
  IF TG_OP = 'INSERT' THEN
    IF est <> 'borrador' THEN
      RAISE EXCEPTION 'Solo se agregan clientes a una corrida en borrador (estado: %)', est;
    END IF;
    SELECT c.numero INTO otra FROM corrida_entidades ce JOIN corridas c ON c.id = ce.corrida_id
      WHERE ce.tenant_id = NEW.tenant_id AND ce.entidad_id = NEW.entidad_id AND ce.etapa = NEW.etapa
        AND ce.corrida_id <> NEW.corrida_id
        AND c.estado IN ('borrador', 'pendiente_aprobacion', 'aprobada', 'lanzada', 'medicion_en_curso')
      LIMIT 1;
    IF otra IS NOT NULL THEN
      RAISE EXCEPTION 'El cliente ya está en la corrida abierta % de la misma etapa', otra;
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'DELETE' THEN
    IF est <> 'borrador' THEN
      RAISE EXCEPTION 'La lista de una corrida que no está en borrador no se modifica (estado: %)', est;
    END IF;
    RETURN OLD;
  END IF;
  IF (NEW.corrida_id, NEW.tenant_id, NEW.entidad_id, NEW.etapa, NEW.grupo, NEW.banda, NEW.segmento, NEW.puntaje,
      NEW.motivo, NEW.is_demo)
     IS DISTINCT FROM
     (OLD.corrida_id, OLD.tenant_id, OLD.entidad_id, OLD.etapa, OLD.grupo, OLD.banda, OLD.segmento, OLD.puntaje,
      OLD.motivo, OLD.is_demo) AND est <> 'borrador' THEN
    RAISE EXCEPTION 'La lista y los grupos de una corrida que no está en borrador no se modifican (estado: %)', est;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER corrida_entidades_proteger BEFORE INSERT OR UPDATE OR DELETE ON corrida_entidades
  FOR EACH ROW EXECUTE FUNCTION corrida_entidades_proteger();

CREATE FUNCTION corrida_excluidos_proteger() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE est text;
BEGIN
  SELECT estado INTO est FROM corridas WHERE id = COALESCE(NEW.corrida_id, OLD.corrida_id);
  IF est <> 'borrador' THEN
    RAISE EXCEPTION 'Los excluidos de una corrida que no está en borrador no se modifican (estado: %)', est;
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$;
CREATE TRIGGER corrida_excluidos_proteger BEFORE INSERT OR UPDATE OR DELETE ON corrida_excluidos
  FOR EACH ROW EXECUTE FUNCTION corrida_excluidos_proteger();

-- ---------------------------------------------------------------- RLS y permisos
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['usuarios', 'acciones_catalogo', 'corridas', 'corrida_entidades', 'corrida_excluidos',
                           'salidas', 'entregas_webhook'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY aislamiento ON %I USING (tenant_id = app_tenant()) WITH CHECK (tenant_id = app_tenant())', t);
  END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON usuarios TO datapulso_app;
GRANT SELECT, INSERT ON acciones_catalogo, entregas_webhook TO datapulso_app;
GRANT SELECT, INSERT, UPDATE ON corridas, salidas TO datapulso_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON corrida_entidades, corrida_excluidos TO datapulso_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO datapulso_app;
