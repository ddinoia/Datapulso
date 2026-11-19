"""Línea de comandos del hito 1:  python -m datapulso <comando>   (o `datapulso <comando>`)

  migrar                         crea la base, el rol de la app y aplica migraciones
  generar-datos  INDUSTRIA       genera CSV de prueba (servicios | salud | financiera)
  demo INDUSTRIA                 crea una organización de demo y la carga por la tubería real (csv o bd)
  kpis TENANT                    muestra KPIs y funnel calculados desde la base (TENANT = nombre o id)
  calidad TENANT                 resumen de calidad y últimas cargas
  config-pendientes TENANT       qué falta configurar (ROI obligatorio)
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
    for n in ("kpis", "calidad", "config-pendientes"):
        x = sp.add_parser(n); x.add_argument("tenant")
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


if __name__ == "__main__":
    main()
