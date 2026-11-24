-- DataPulso · migración 002 · motor de puntaje (hito 2)
-- `modelos`: cada entrenamiento crea una versión inmutable con sus parámetros (en JSON legible, sin pickles),
--            la calidad medida en el backtest temporal y los umbrales de banda.
-- `puntajes`: puntaje, banda y drivers por cliente, etapa y fecha de cálculo. Guarda la versión del modelo
--             y de la configuración con la que se calculó.

CREATE TABLE modelos (
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  version          text NOT NULL,                  -- '<etapa>-v<n>'
  etapa            text NOT NULL,
  numero           integer NOT NULL,
  metodo           text NOT NULL CHECK (metodo IN ('modelo', 'regla')),
  motivo_metodo    text,                           -- por qué se usó la regla simple, si corresponde
  fecha_referencia date NOT NULL,                  -- último día con datos al entrenar
  config_version   integer NOT NULL,
  definicion       jsonb NOT NULL,                 -- población, evento modelado, ventana, cortes de entrenamiento
  parametros       jsonb NOT NULL,                 -- variables, medias, escalas, coeficientes (o pesos de la regla)
  calidad          jsonb NOT NULL,                 -- backtest: AUC, tasa del evento por banda, comparación con la regla
  umbrales_banda   jsonb,                          -- cortes de terciles sobre la población puntuada
  is_demo          boolean NOT NULL DEFAULT false,
  creado_por       text NOT NULL,
  creado_en        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, version),
  UNIQUE (tenant_id, etapa, numero)
);
CREATE TRIGGER modelos_inmutable BEFORE UPDATE OR DELETE ON modelos
  FOR EACH ROW EXECUTE FUNCTION bloquear_modificacion();

CREATE TABLE puntajes (
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  entidad_id      uuid NOT NULL,
  etapa           text NOT NULL,
  fecha           date NOT NULL,                   -- fecha de corte del cálculo
  puntaje         numeric(6,5) NOT NULL CHECK (puntaje BETWEEN 0 AND 1),
  banda           text NOT NULL CHECK (banda IN ('alta', 'media', 'baja')),
  drivers         jsonb NOT NULL DEFAULT '[]',
  metodo          text NOT NULL CHECK (metodo IN ('modelo', 'regla')),
  modelo_version  text NOT NULL,
  config_version  integer NOT NULL,
  is_demo         boolean NOT NULL DEFAULT false,
  creado_en       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, etapa, fecha, entidad_id),
  FOREIGN KEY (tenant_id, entidad_id) REFERENCES entidades (tenant_id, id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, modelo_version) REFERENCES modelos (tenant_id, version)
);
CREATE INDEX puntajes_entidad_idx ON puntajes (tenant_id, entidad_id, etapa, fecha DESC);
CREATE INDEX puntajes_banda_idx ON puntajes (tenant_id, etapa, fecha, banda, puntaje DESC);

ALTER TABLE modelos ENABLE ROW LEVEL SECURITY;
CREATE POLICY aislamiento ON modelos USING (tenant_id = app_tenant()) WITH CHECK (tenant_id = app_tenant());
ALTER TABLE puntajes ENABLE ROW LEVEL SECURITY;
CREATE POLICY aislamiento ON puntajes USING (tenant_id = app_tenant()) WITH CHECK (tenant_id = app_tenant());

GRANT SELECT, INSERT ON modelos TO datapulso_app;
-- DELETE: recalcular el mismo día reemplaza los puntajes de esa fecha y etapa (el historial de otras fechas queda).
GRANT SELECT, INSERT, DELETE ON puntajes TO datapulso_app;
