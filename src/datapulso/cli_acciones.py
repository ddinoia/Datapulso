"""Comandos del hito 3 (acciones y corridas). Se registran desde cli.py.

  usuarios TENANT                         usuarios y roles
  usuario-alta TENANT NOMBRE ROL          alta de usuario (el primero puede crearse sin --usuario: bootstrap)
  catalogo TENANT [--cargar-plantilla]    acciones vigentes (y cargar las de ejemplo de la industria)
  valor-economico TENANT --etapa E --valor V   valor de un resultado (crea una versión de configuración)
  salida-webhook TENANT NOMBRE URL        habilitar webhook / tareas humanas (muestra el secreto una sola vez)
  recomendar TENANT --etapa E [--cliente ID]   las tres mejores acciones por cliente, con el motivo
  corrida-crear TENANT --accion COD       crea la corrida en borrador [--bandas alta,media] [--limite N] [--semilla S]
  corrida-pedir-aprobacion TENANT C       pasa a pendiente de aprobación
  cola-aprobacion TENANT                  corridas pendientes de aprobación
  corrida-aprobar TENANT C                aprueba y congela la proyección
  corrida-cancelar TENANT C --motivo M
  corrida-exportar TENANT C [--carpeta D] CSV de tratamiento y de control con hash
  corrida-emitir TENANT C                 webhook / tarea humana (solo corridas aprobadas y salida habilitada)
  corrida-lanzar TENANT C --fecha F       la fecha real en que el cliente ejecutó la acción
  corridas TENANT [--estado E]            historial
  corrida TENANT C                        detalle: diseño, exclusiones y proyección
Todos los que cambian algo piden --usuario (queda en auditoría).
"""
import json
import sys
from datetime import date
from .textos import t
from .puntaje.presentacion import _num


def registrar(sp) -> None:
    x = sp.add_parser("usuarios"); x.add_argument("tenant")
    x = sp.add_parser("usuario-alta"); x.add_argument("tenant"); x.add_argument("nombre"); x.add_argument("rol")
    x.add_argument("--usuario")
    x = sp.add_parser("catalogo"); x.add_argument("tenant"); x.add_argument("--cargar-plantilla", action="store_true")
    x.add_argument("--usuario")
    x = sp.add_parser("valor-economico"); x.add_argument("tenant"); x.add_argument("--etapa", required=True)
    x.add_argument("--valor", type=float, required=True); x.add_argument("--usuario", required=True)
    x = sp.add_parser("salida-webhook"); x.add_argument("tenant"); x.add_argument("nombre"); x.add_argument("url")
    x.add_argument("--usuario", required=True)
    x = sp.add_parser("recomendar"); x.add_argument("tenant"); x.add_argument("--etapa", required=True)
    x.add_argument("--cliente"); x.add_argument("--limite", type=int, default=5)
    x = sp.add_parser("corrida-crear"); x.add_argument("tenant"); x.add_argument("--accion", required=True)
    x.add_argument("--usuario", required=True); x.add_argument("--bandas"); x.add_argument("--segmentos")
    x.add_argument("--limite", type=int); x.add_argument("--semilla", type=int)
    for n in ("corrida-pedir-aprobacion", "corrida-aprobar", "corrida-emitir"):
        x = sp.add_parser(n); x.add_argument("tenant"); x.add_argument("corrida"); x.add_argument("--usuario", required=True)
    x = sp.add_parser("corrida-cancelar"); x.add_argument("tenant"); x.add_argument("corrida")
    x.add_argument("--usuario", required=True); x.add_argument("--motivo", required=True)
    x = sp.add_parser("corrida-exportar"); x.add_argument("tenant"); x.add_argument("corrida")
    x.add_argument("--usuario", required=True); x.add_argument("--carpeta", default="out/exportaciones")
    x = sp.add_parser("corrida-lanzar"); x.add_argument("tenant"); x.add_argument("corrida")
    x.add_argument("--usuario", required=True); x.add_argument("--fecha", type=date.fromisoformat, required=True)
    x = sp.add_parser("cola-aprobacion"); x.add_argument("tenant")
    x = sp.add_parser("corridas"); x.add_argument("tenant"); x.add_argument("--estado")
    x = sp.add_parser("corrida"); x.add_argument("tenant"); x.add_argument("corrida")


COMANDOS = {"usuarios", "usuario-alta", "catalogo", "valor-economico", "salida-webhook", "recomendar", "corrida-crear",
            "corrida-pedir-aprobacion", "corrida-aprobar", "corrida-emitir", "corrida-cancelar", "corrida-exportar",
            "corrida-lanzar", "cola-aprobacion", "corridas", "corrida"}


def _pct(x) -> str:
    return "s/d" if x is None else ("−" if float(x) < 0 else "") + _num(100 * abs(float(x)), 1) + " %"


def _pts(x) -> str:
    return _num(100 * float(x), 1) + " puntos"


def _pesos(x) -> str:
    return "s/d" if x is None else ("−" if x < 0 else "") + "$ " + _num(abs(float(x)), 0)


def _fila_corrida(c: dict) -> str:
    return (f"  C-{c['numero']:<4} {t('acciones.estado.' + c['estado']):<24} {c['codigo']:<22} {c['etapa']:<11} "
            f"trat {c['diseno'].get('tratamiento', '?'):>5} · ctrl {c['diseno'].get('control', '?'):>4} · "
            f"creada por {c['creada_por_nombre']}" + (f" · aprobada por {c['aprobada_por_nombre']}" if c.get("aprobada_por_nombre") else ""))


def imprimir_proyeccion(p: dict) -> None:
    e, r = p["esperado"], p["rango"]
    a = p["accion"]
    print(f"  Proyección {'CONGELADA ' + p.get('congelada_en', '')[:19] if p.get('congelada') else '(preliminar: se congela al aprobar)'}")
    print(f"    Acción {a['codigo']} v{a['version']} · impacto {_pct(a['impacto_esperado'])} "
          f"({t('acciones.impacto_' + a['impacto_origen'])}) · modelo {p['versiones']['modelo']} · config v{p['versiones']['config']}"
          f" · puntajes del {p['versiones']['fecha_puntajes']}")
    ta = p["tasa_esperada_sin_accion"]
    print(f"    Tratamiento {p['tamanos']['tratamiento']} · control {p['tamanos']['control']} · tasa esperada sin acción "
          f"{_pct(ta['total'])} (tratamiento {_pct(ta['tratamiento'])}, control {_pct(ta['control'])})")
    print(f"    Efecto esperado {_pts(e['efecto_esperado'])} (rango {_pts(r['bajo']['efecto_esperado'])} a "
          f"{_pts(r['alto']['efecto_esperado'])}) · eventos evitados {_num(e['eventos_evitados'])} "
          f"({_num(r['bajo']['eventos_evitados'])} a {_num(r['alto']['eventos_evitados'])})")
    print(f"    Valor esperado {_pesos(e['valor_esperado'])} · costo {_pesos(p['costo_total'])} · ROI esperado "
          f"{_pct(e['roi_esperado'])} (rango {_pct(r['bajo']['roi_esperado'])} a {_pct(r['alto']['roi_esperado'])})")
    for b in p["por_banda"]:
        print(f"      {t('banda.' + b['banda']):<16} {b['clientes']:>5} clientes · tasa sin acción {_pct(b['tasa_sin_accion'])}"
              f" · evitados {_num(b['eventos_evitados'])}")
    cal = p.get("calibracion_modelo")
    if cal:
        estado = (t("calibracion.no_evaluable") if not cal["evaluable"]
                  else t("calibracion.cumple") if cal["cumple"] else t("calibracion.no_cumple"))
        print(f"    Calibración del modelo en el backtest: error máximo {_num(100 * cal['error_max'])} puntos · {estado}")


def ejecutar(a, tenant_conn, tenant_id) -> None:
    from .acciones import ErrorAcciones, usuarios, catalogo, configurar, recomendacion, corridas, exportar, webhook
    try:
        with tenant_conn(tenant_id(a.tenant)) as c:
            _ejecutar(a, c, usuarios, catalogo, configurar, recomendacion, corridas, exportar, webhook)
            c.commit()
    except ErrorAcciones as e:
        sys.exit(f"No se pudo: {e}")


def _ejecutar(a, c, usuarios, catalogo, configurar, recomendacion, corridas, exportar, webhook) -> None:
    from .tenants import tenant_info
    if a.cmd == "usuarios":
        for u in usuarios.listar(c):
            print(f"  {u['nombre']:<20} {t('acciones.rol.' + u['rol']):<14} {'activo' if u['activo'] else 'inactivo'}")
    elif a.cmd == "usuario-alta":
        hay = c.execute("SELECT count(*) AS n FROM usuarios").fetchone()["n"]
        if hay:   # el primero es el bootstrap (debe ser administrador); después hace falta un administrador
            if not a.usuario:
                sys.exit("Falta --usuario (un administrador)")
            usuarios.exigir(c, a.usuario, "administrar")
        elif a.rol != "administrador":
            sys.exit("El primer usuario de la organización tiene que ser administrador")
        u = usuarios.alta(c, a.nombre, a.rol, a.usuario or "bootstrap")
        print(f"usuario {u['nombre']} ({t('acciones.rol.' + u['rol'])}) creado")
    elif a.cmd == "catalogo":
        if a.cargar_plantilla:
            usuarios.exigir(c, a.usuario or "", "administrar")
            nuevas = catalogo.cargar_plantilla(c, tenant_info(c)["industria"], a.usuario)
            print(f"{len(nuevas)} acciones de ejemplo cargadas (impacto esperado: {t('acciones.impacto_supuesto')})")
        for x in catalogo.vigentes(c):
            el = x["elegibilidad"].get("bandas") or ["todas"]
            print(f"  {x['codigo']:<24} v{x['version']} {x['etapa']:<11} {t('acciones.tipo.' + x['tipo']):<18} {x['canal']:<9} "
                  f"costo {_pesos(x['costo_unitario']):>9} · impacto {_pct(x['impacto_esperado'])} "
                  f"({t('acciones.impacto_' + x['impacto_origen'])}) · bandas {','.join(el)}"
                  + (" · " + t("acciones.modifica_sistema") if x["modifica_sistema_cliente"] else ""))
    elif a.cmd == "valor-economico":
        v = configurar.valor_economico(c, a.usuario, a.etapa, a.valor)
        print(f"configuración v{v}: valor de un resultado en {a.etapa} = {_pesos(a.valor)}")
    elif a.cmd == "salida-webhook":
        s, secreto = webhook.configurar_salida(c, a.nombre, a.url, a.usuario)
        print(f"salida {s['nombre']} habilitada para {', '.join(s['tipos'])}")
        print(f"secreto HMAC (se muestra una sola vez; guardarlo en el receptor): {secreto}")
    elif a.cmd == "recomendar":
        r = recomendacion.recomendar(c, a.etapa, a.cliente, a.limite)
        print(f"\nRecomendación · {a.etapa} · puntajes del {r['fecha_puntajes']} ({r['modelo_version']})"
              + ("" if r["valor_configurado"] else " · SIN valor económico: se ordena por eventos evitados"))
        print("  Mejor acción por cantidad de clientes: " + ", ".join(f"{k} {v}" for k, v in r["mejor_accion_por_cantidad"].items()))
        for cl in r["clientes"]:
            print(f"\n  {cl['id_externo']}  {t('banda.' + cl['banda'])} · probabilidad {_pct(cl['puntaje'])} · {cl['segmento']}")
            for k, o in enumerate(cl["opciones"], 1):
                print(f"     {k}. {o['codigo']}: {o['motivo']}")
    elif a.cmd == "corrida-crear":
        x = corridas.crear(c, a.accion, a.usuario, bandas=a.bandas.split(",") if a.bandas else None,
                           segmentos=a.segmentos.split(",") if a.segmentos else None, limite=a.limite, semilla=a.semilla)
        _detalle(c, corridas, x)
    elif a.cmd == "corrida-pedir-aprobacion":
        x = corridas.pedir_aprobacion(c, a.corrida, a.usuario)
        print(f"C-{x['numero']}: {t('acciones.estado.' + x['estado'])}")
    elif a.cmd == "corrida-aprobar":
        x = corridas.aprobar(c, a.corrida, a.usuario)
        print(f"C-{x['numero']}: {t('acciones.estado.' + x['estado'])} por {a.usuario}")
        imprimir_proyeccion(x["proyeccion"])
    elif a.cmd == "corrida-cancelar":
        x = corridas.cancelar(c, a.corrida, a.usuario, a.motivo)
        print(f"C-{x['numero']}: {t('acciones.estado.' + x['estado'])} ({x['motivo_cancelacion']})")
    elif a.cmd == "corrida-exportar":
        for g, v in exportar.exportar(c, a.corrida, a.usuario, a.carpeta).items():
            print(f"  {t('acciones.grupo.' + g):<12} {v['filas']:>6} filas · {v['ruta']} · sha256 {v['sha256']}")
    elif a.cmd == "corrida-emitir":
        r = webhook.emitir(c, a.corrida, a.usuario)
        print(f"  lotes {r['lotes']} · entregados {r['entregados']} · ya entregados antes {r['ya_entregados']} · "
              f"fallidos {r['fallidos']} · intentos {r['intentos']}")
    elif a.cmd == "corrida-lanzar":
        x = corridas.lanzar(c, a.corrida, a.usuario, a.fecha)
        print(f"C-{x['numero']}: {t('acciones.estado.' + x['estado'])} el {x['fecha_lanzamiento']} (desde ahí corre la ventana de medición)")
    elif a.cmd == "cola-aprobacion":
        filas = corridas.cola_aprobacion(c)
        print("Sin corridas pendientes de aprobación" if not filas else "\n".join(_fila_corrida(x) for x in filas))
    elif a.cmd == "corridas":
        filas = corridas.listar(c, a.estado)
        print("Sin corridas" if not filas else "\n".join(_fila_corrida(x) for x in filas))
    elif a.cmd == "corrida":
        _detalle(c, corridas, corridas.obtener(c, a.corrida))


def _detalle(c, corridas, x: dict) -> None:
    d = x["diseno"]
    print(f"\nC-{x['numero']} · {t('acciones.estado.' + x['estado'])} · etapa {x['etapa']} · semilla {x['semilla']} · "
          f"control {_num(float(x['pct_control']))} %")
    print(f"  Puntuados {d['puntuados']} · elegibles para la acción {d['elegibles']} · excluidos por la política "
          f"{x['exclusiones']['total']}")
    from .tenants import config_activa
    accion = c.execute("SELECT frecuencia_max_dias FROM acciones_catalogo WHERE id = %s", (x["accion_id"],)).fetchone()
    tope = config_activa(c)[1].politica_contacto.max_contactos_por_cliente_30d
    for m, n in x["exclusiones"]["por_motivo"].items():
        print(f"     - {n} {t('acciones.exclusion.' + m, dias=accion['frecuencia_max_dias'], tope=tope)}")
    print(f"  Efecto mínimo detectable ≈ {_pts(d['efecto_minimo_detectable'])} · efecto esperado {_pts(d['efecto_esperado'])}")
    if d.get("aviso"):
        print(f"  Aviso: {d['aviso']}")
    if x["estado"] == "cancelada":
        print(f"  Cancelada: {x['motivo_cancelacion']}")
    if x["proyeccion"]:
        imprimir_proyeccion(x["proyeccion"])
