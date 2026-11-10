"""Añade al libro financiero las hojas de configuración que lee el informe de Power BI.

    .venv\\Scripts\\python.exe bi\\preparar_libro.py            (usa LIBRO_RUTA del .env)
    .venv\\Scripts\\python.exe bi\\preparar_libro.py "C:\\...\\libro.xlsx"

Crea, sólo si no existen:
  - Proyectos  (tblProyectos)  identidad de cada informe: color, nombre, logos, símbolo. Como dim_marca_proyecto.
  - Supuestos  (tblSupuestos)  salario, deducciones, presupuesto de gastos fijos... (los % se escriben como 4 = 4 %)
  - Metas      (tblMetas)      metas de ahorro con monto y fecha

Antes de tocar el libro deja una copia en respaldos/. Si el libro está abierto en Excel, no escribe.
Es idempotente: si las tablas ya están, no hace nada (los valores que hayas cambiado se respetan).
"""
from __future__ import annotations

import shutil
import sys
from datetime import date, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from finanzas_nu.comun.libro_excel import Columna, crear_tabla, libro_abierto, tabla_existe  # noqa: E402

ID = "001"

PROYECTOS = (
    "Proyectos", "tblProyectos",
    [Columna("id_proyecto", ancho=12), Columna("nombre_comercial", ancho=38), Columna("titular", ancho=30),
     Columna("color_primario", ancho=14), Columna("simbolo_moneda", ancho=10), Columna("logo_url", ancho=60),
     Columna("logo_modo", ancho=10), Columna("logo_px", "entero", ancho=9), Columna("logo_marca_url", ancho=60),
     Columna("marca_nombre", ancho=24), Columna("marca_lema", ancho=40)],
    [{"id_proyecto": ID, "nombre_comercial": "Mis ahorros",
      "titular": "Tu nombre", "color_primario": "#4F8CC3", "simbolo_moneda": "$",
      "logo_url": "https://img.icons8.com/fluency-systems-filled/96/user-male-circle.png", "logo_modo": "alto", "logo_px": 64,
      "logo_marca_url": "https://static.vecteezy.com/system/resources/previews/024/607/637/non_2x/"
                        "money-icon-coins-illustration-sign-finance-symbol-saving-logo-vector.jpg",
      "marca_nombre": "Finanzas Personales", "marca_lema": "Ingresos, gastos y ahorro"}],
)

_S = [  # clave, parámetro, valor, unidad, nota
    ("SALARIO_BRUTO", "Salario mensual bruto", 3000000, "COP", "Salario básico, sin deducciones."),
    ("AUX_TRANSPORTE", "Auxilio de transporte", 0, "COP",
     "Si tu empleador lo paga (salario de hasta 2 SMMLV), escribe aquí el valor mensual."),
    ("SALUD", "Aporte a salud (empleado)", 4, "%", "Se descuenta del salario bruto."),
    ("PENSION", "Aporte a pensión (empleado)", 4, "%", "Se descuenta del salario bruto."),
    ("FSP", "Fondo de solidaridad pensional", 0, "%", "1 % a partir de 4 SMMLV."),
    ("RETENCION", "Retención en la fuente mensual", 0, "COP", "Valor mensual que te retienen, si aplica."),
    ("OTRAS_DEDUCCIONES", "Otras deducciones mensuales", 0, "COP", "Libranzas, fondo de empleados, etc."),
    ("GASTO_FIJO_PRESUPUESTO", "Gastos fijos mensuales (presupuesto)", 1350000, "COP",
     "Lo que esperas gastar cada mes en gastos fijos; el informe lo compara con lo real."),
    ("RENTABILIDAD_EA", "Rentabilidad de la cuenta (efectiva anual)", 0, "%",
     "Sólo para proyectar la meta. 0 = proyectar sin rendimientos."),
    ("SALDO_INICIAL", "Saldo antes del primer movimiento del libro", 0, "COP",
     "Lo que había en la cuenta Nu antes del primer extracto importado."),
    ("AJUSTE_META", "Ajuste al ahorro que cuenta para la meta", 0, "COP",
     "Negativo si parte del saldo de Nu no es tuyo; positivo si ahorras también en otra parte."),
]
SUPUESTOS = (
    "Supuestos", "tblSupuestos",
    [Columna("id_proyecto", ancho=12), Columna("Clave", ancho=26), Columna("Parámetro", ancho=44),
     Columna("Valor", ancho=16), Columna("Unidad", ancho=9), Columna("Nota", ancho=80)],
    [{"id_proyecto": ID, "Clave": c, "Parámetro": p, "Valor": v, "Unidad": u, "Nota": n} for c, p, v, u, n in _S],
)

METAS = (
    "Metas", "tblMetas",
    [Columna("id_proyecto", ancho=12), Columna("Meta", ancho=30), Columna("Monto objetivo", "moneda", ancho=18),
     Columna("Fecha inicio", "fecha", ancho=13), Columna("Fecha objetivo", "fecha", ancho=14),
     Columna("Activa", ancho=8)],
    [{"id_proyecto": ID, "Meta": "Fondo de emergencia", "Monto objetivo": 15000000,
      "Fecha inicio": date(2025, 9, 1), "Fecha objetivo": date(2027, 12, 31), "Activa": "Sí"}],
)


def ruta_libro() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    env = RAIZ / ".env"
    for linea in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        if linea.startswith("LIBRO_RUTA="):
            return Path(linea.split("=", 1)[1].strip().strip('"'))
    raise SystemExit("No sé qué libro usar: pasa la ruta o pon LIBRO_RUTA en .env")


def main():
    libro = ruta_libro()
    if not libro.exists():
        raise SystemExit(f"No existe {libro}")
    if libro_abierto(libro):
        raise SystemExit("El libro está abierto en Excel: ciérralo y vuelve a correr esto.")
    faltan = [s for s in (PROYECTOS, SUPUESTOS, METAS) if not tabla_existe(libro, s[1])]
    if not faltan:
        print("Las tres tablas ya existen; no hay nada que hacer.")
        return
    resp = RAIZ / "respaldos" / f"{libro.stem}_{datetime.now():%Y-%m-%d_%H-%M-%S}{libro.suffix}"
    resp.parent.mkdir(exist_ok=True)
    shutil.copy2(libro, resp)
    print(f"Respaldo: {resp}")
    for hoja, tabla, columnas, filas in faltan:
        crear_tabla(libro, hoja, tabla, columnas, filas, color="FF4F8CC3")
        print(f"  + hoja '{hoja}' con {tabla} ({len(filas)} filas)")


if __name__ == "__main__":
    main()
