"""Línea de comandos:  python -m datapulso <comando>   (o `datapulso <comando>`)

  migrar                         crea la base, el rol de la app y aplica migraciones
  generar-datos  INDUSTRIA       genera CSV de prueba (servicios | salud | financiera)
  demo INDUSTRIA                 crea una organización de demo y la carga por la tubería real (csv o bd)
  tenants                        lista las organizaciones: nombre, id, industria, demo, versión de config, fecha de alta
  kpis TENANT                    muestra KPIs y funnel calculados desde la base (TENANT = nombre o id)
  calidad TENANT                 resumen de calidad y últimas cargas
  config-pendientes TENANT       qué falta configurar (ROI obligatorio)

Hito 2 (motor de puntaje):
  puntuar TENANT                 proceso nocturno de un tenant: reentrena si hace falta y puntúa todas las etapas
     [--etapa E] [--reentrenar] [--fecha AAAA-MM-DD]
  puntuar-todos                  proceso nocturno para todos los tenants (lo que correría el programador de tareas)
  modelo TENANT --etapa E        versión vigente del modelo: qué predice, método y calidad del backtest
  clientes TENANT --etapa E      lista de clientes con puntaje, banda y drivers
     [--banda alta|media|baja] [--limite N]
"""
import argparse
import json
import sys
from datetime import date
from .db import admin_conn, tenant_conn


def _tenant_id(ref: str) -> str:
    with admin_conn() as c:
        r = c.execute("SELECT id FROM tenants WHERE nombre=%s OR id::text=%s", (ref, ref)).fetchone()
    if not r:
        sys.exit(f"No existe el tenant '{ref}'")
    return str(r["id"])


def _imprimir_kpis(k: dict) -> None:
    print(f"\n{k['tenant']} · {k['industria']} · corte {k['fecha_corte']} · config v{k['config_version']}"
          + ("  [DATOS DE PRUEBA]" if k["es_demo"] else ""))
    for nombre, v in k["resumen"].items():
        print(f"  {nombre}: {v}")
    print("\n  Funnel")
    for f in k["funnel"]:
        tasa = f"{100 * f['tasa']:.1f}%" if f["tasa"] is not None else "s/d"
        extra = f" · impago ${f['monto_vencido_impago']:,.0f}" if "monto_vencido_impago" in f else ""
        print(f"    {f['etiqueta']:<14} {f['alcanzaron']:>7} / {f['elegibles']:<7} {tasa:>7}  ({f['regla']}){extra}")
    if "segmentos" in k:
        print(f"\n  Por {k['segmentos']['atributo']}")
        for s in k["segmentos"]["filas"]:
            print(f"    {s['segmento']:<20} {s['clientes']:>6} clientes · {s['bajas']} bajas")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="datapulso", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("migrar")
    g = sp.add_parser("generar-datos"); g.add_argument("industria"); g.add_argument("--salida", default="out/datos")
    g.add_argument("--clientes", type=int); g.add_argument("--semilla", type=int, default=7); g.add_argument("--estilo", default="iso", choices=["iso", "ar"])
    d = sp.add_parser("demo"); d.add_argument("industria"); d.add_argument("--via", default="csv", choices=["csv", "bd"])
    d.add_argument("--motor", default="postgresql", choices=["postgresql", "mysql"]); d.add_argument("--clientes", type=int)
    d.add_argument("--semilla", type=int, default=7); d.add_argument("--estilo", default="iso", choices=["iso", "ar"])
    sp.add_parser("tenants")
    for n in ("kpis", "calidad", "config-pendientes"):
        x = sp.add_parser(n); x.add_argument("tenant")
    pu = sp.add_parser("puntuar"); pu.add_argument("tenant"); pu.add_argument("--etapa")
    pu.add_argument("--reentrenar", action="store_true"); pu.add_argument("--fecha", type=date.fromisoformat)
    pt = sp.add_parser("puntuar-todos"); pt.add_argument("--reentrenar", action="store_true")
    mo = sp.add_parser("modelo"); mo.add_argument("tenant"); mo.add_argument("--etapa", required=True)
    cl = sp.add_parser("clientes"); cl.add_argument("tenant"); cl.add_argument("--etapa", required=True)
    cl.add_argument("--banda", choices=["alta", "media", "baja"]); cl.add_argument("--limite", type=int, default=20)
    a = ap.parse_args(argv)

    if a.cmd == "migrar":
        from .migrate import migrate
        print("migraciones aplicadas:", migrate() or "ninguna (ya estaba al día)")
    elif a.cmd == "generar-datos":
        from .datagen.generador import generar, exportar_csv
        ds = generar(a.industria, a.semilla, a.clientes)
        print(json.dumps(exportar_csv(ds, a.salida, a.estilo), indent=1))
    elif a.cmd == "demo":
        from .datagen import demo
        from . import kpis
        if a.via == "csv":
            r = demo.cargar_demo_csv(a.industria, n_clientes=a.clientes, semilla=a.semilla, estilo=a.estilo)
        else:
            r = demo.cargar_demo_bd(a.industria, motor=a.motor, n_clientes=a.clientes, semilla=a.semilla)
        for t, i in r["informes"].items():
            print(f"carga {t}: {i['filas']}")
        with tenant_conn(r["tenant_id"]) as c:
            _imprimir_kpis(kpis.calcular(c))
        print("\ntenant:", r["tenant_id"])
    elif a.cmd == "tenants":
        # Operación de administración: lista todas las organizaciones (el rol de la app solo ve la suya por RLS).
        with admin_conn() as c:
            filas = c.execute("""SELECT nombre, id, industria, is_demo, config_version_activa, creado_en
                                 FROM tenants ORDER BY creado_en, nombre""").fetchall()
        if not filas:
            print("No hay organizaciones. Crear una con `datapulso demo <industria>`.")
        else:
            print(f"{'nombre':<34} {'id':<36}  {'industria':<11} {'demo':<4}  config  creado")
            for r in filas:
                print(f"{r['nombre']:<34} {str(r['id']):<36}  {r['industria']:<11} {'sí' if r['is_demo'] else 'no':<4}  "
                      f"v{r['config_version_activa'] or '-':<5} {r['creado_en']:%Y-%m-%d %H:%M}")
    elif a.cmd == "kpis":
        from . import kpis
        with tenant_conn(_tenant_id(a.tenant)) as c:
            _imprimir_kpis(kpis.calcular(c))
    elif a.cmd == "calidad":
        from . import kpis
        with tenant_conn(_tenant_id(a.tenant)) as c:
            print(json.dumps(kpis.calidad_datos(c), indent=1, ensure_ascii=False))
            for r in c.execute("""SELECT f.nombre, ca.estado, ca.filas_leidas, ca.filas_aceptadas, ca.filas_rechazadas,
                                  ca.filas_insertadas, ca.filas_actualizadas, ca.inicio FROM cargas ca JOIN fuentes f ON f.id=ca.fuente_id
                                  ORDER BY ca.inicio DESC LIMIT 15"""):
                print(dict(r))
    elif a.cmd == "config-pendientes":
        from .tenants import config_activa
        from .config import pendientes
        with tenant_conn(_tenant_id(a.tenant)) as c:
            v, cfg = config_activa(c)
            p = pendientes(cfg)
            print(f"config v{v}: " + ("completa" if not p else f"{len(p)} pendientes"))
            for x in p:
                print(" -", x)
    elif a.cmd == "puntuar":
        from .puntaje import motor
        with tenant_conn(_tenant_id(a.tenant)) as c:
            if a.etapa:
                m = motor.modelo_vigente(c, a.etapa)
                if a.reentrenar or not m or not motor.vigente_para_variables(m):
                    m = motor.entrenar(c, a.etapa, a.fecha)
                res = [motor.puntuar(c, a.etapa, a.fecha, m["version"])]
                c.commit()
            else:
                res = motor.proceso_nocturno(c, a.fecha, forzar_entrenamiento=a.reentrenar)
            _imprimir_puntuacion(c, res)
    elif a.cmd == "puntuar-todos":
        from .puntaje import motor
        with admin_conn() as c:
            tenants = [(str(r["id"]), r["nombre"]) for r in c.execute("SELECT id, nombre FROM tenants ORDER BY nombre")]
        for tid, nombre in tenants:
            print(f"\n== {nombre}")
            try:
                with tenant_conn(tid) as c:
                    _imprimir_puntuacion(c, motor.proceso_nocturno(c, forzar_entrenamiento=a.reentrenar))
            except Exception as e:  # un tenant con problemas no frena a los demás
                print(f"  error: {e}")
    elif a.cmd == "modelo":
        from .puntaje import motor, presentacion
        with tenant_conn(_tenant_id(a.tenant)) as c:
            m = motor.modelo_vigente(c, a.etapa)
            if not m:
                sys.exit(f"No hay modelo para '{a.etapa}': correr `datapulso puntuar {a.tenant}`")
            from .tenants import config_activa
            print("\n".join(presentacion.resumen_modelo(m, config_activa(c)[1].vocabulario.cliente)))
            if m["metodo"] == "modelo":
                print("  Variables con más peso (coeficiente sobre la variable estandarizada):")
                coef = sorted(m["parametros"]["coef"].items(), key=lambda kv: -abs(kv[1]))[:10]
                for k, v in coef:
                    nombre = k.split("=")[0]
                    nivel = f" = {k.split('=', 1)[1]}" if "=" in k else ""
                    print(f"    {v:+.2f}  {presentacion.etiqueta_variable(nombre)}{nivel}")
    elif a.cmd == "clientes":
        from .puntaje import motor, presentacion
        from .tenants import config_activa
        from .textos import t
        with tenant_conn(_tenant_id(a.tenant)) as c:
            _, cfg = config_activa(c)
            filas = motor.lista_clientes(c, a.etapa, a.banda, a.limite)
            if not filas:
                sys.exit(f"No hay puntajes de '{a.etapa}': correr `datapulso puntuar {a.tenant}`")
            f0 = filas[0]
            print(f"\n{cfg.vocabulario.clientes} · {cfg.vocabulario.etapas.get(a.etapa, a.etapa)} · {f0['fecha']} · "
                  f"{f0['modelo_version']} ({t('metodo.' + f0['metodo'])}) · config v{f0['config_version']}")
            seg = cfg.segmento_atributo
            for f in filas:
                extra = f" · {seg}: {f['atributos'].get(seg)}" if seg and f["atributos"].get(seg) else ""
                emp = f" · empresa {f['empresa']}" if f["empresa"] else ""
                print(f"\n  {f['id_externo']}  {presentacion.texto_banda(f['banda'], f['puntaje'], f['metodo'])}"
                      f"{extra}{emp}")
                for d in f["drivers"][:3]:
                    print(f"     - {presentacion.texto_driver(d)}")


def _imprimir_puntuacion(conn, res: list[dict]) -> None:
    from .puntaje import motor, presentacion
    from .tenants import config_activa
    cliente = config_activa(conn)[1].vocabulario.cliente
    for r in res:
        if "omitida" in r:
            print(f"  {r['etapa']}: omitida ({r['omitida']})")
            continue
        print(f"\n  {r['etapa']}: {r['puntuados']} puntuados al {r['fecha']} · bandas {r.get('bandas')}"
              + (" · modelo reentrenado" if r.get("entrenado") else ""))
        print("\n".join("  " + x for x in presentacion.resumen_modelo(motor.obtener_modelo(conn, r["modelo_version"]), cliente)))


if __name__ == "__main__":
    main()
