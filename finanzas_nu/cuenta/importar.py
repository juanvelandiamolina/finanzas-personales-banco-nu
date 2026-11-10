"""Cuenta Nu (ahorros) → hoja 'Ingresos y Gastos', tabla tblMovimientos."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from ..comun.clasificar import clasificar
from ..comun.libro_excel import Columna
from ..comun.proceso import Importador, clave_texto, cop
from .extracto import cuadrar, leer_extracto_cuenta


class ImportadorCuenta(Importador):
    nombre = "cuenta"
    titulo = "Cuenta Nu"
    hoja = "Ingresos y Gastos"
    tabla = "tblMovimientos"
    archivo_reglas = "reglas_cuenta.json"

    def columnas(self):
        # sólo se usa si el libro no tuviera la tabla (un libro ya preparado la trae)
        f = lambda e: f'IF(tblMovimientos[[#This Row],[Fecha]]="","",{e})'
        return [Columna("Fecha", "fecha", ancho=14), Columna("Categoría", ancho=18), Columna("Subcategoría", ancho=22),
                Columna("Descripción", ancho=40), Columna("Monto (COP)", "moneda", ancho=18),
                Columna("Año", formula=f("YEAR(tblMovimientos[[#This Row],[Fecha]])")),
                Columna("Mes", formula=f("MONTH(tblMovimientos[[#This Row],[Fecha]])")),
                Columna("AAAA-MM", formula=f('YEAR(tblMovimientos[[#This Row],[Fecha]])&"-"&'
                                             'TEXT(MONTH(tblMovimientos[[#This Row],[Fecha]]),"00")'), ancho=12),
                Columna("Cuenta", ancho=12)]

    def leer(self, pdf, clave):
        return leer_extracto_cuenta(pdf, clave)

    def cuadrar(self, ext):
        return cuadrar(ext)

    def periodo(self, ext):
        return ext.desde, ext.hasta, f"período {ext.periodo_texto}"

    def filas(self, ext, reglas, historial, opciones):
        out = []
        for i, m in enumerate(ext.movimientos):
            c = clasificar(m.descripcion, m.monto, reglas, historial)
            out.append({"Fecha": m.fecha, "Categoría": c.categoria, "Subcategoría": c.subcategoria,
                        "Descripción": m.descripcion, "Monto (COP)": abs(m.monto), "Cuenta": opciones.cuenta,
                        "_signo": m.monto, "_origen": c.origen, "_revisar": c.revisar, "_orden": i})
        return out

    def clave(self, f):
        return clave_texto(f.get("Fecha"), f.get("Descripción"), abs(Decimal(str(f.get("Monto (COP)") or 0))))

    def imprimir(self, filas):
        ancho = min(44, max((len(f["Descripción"]) for f in filas), default=10))
        for f in filas:
            nota = " (historial)" if f["_origen"] == "historial" else ""
            print(f"  {f['Fecha']}  {f['Descripción'][:ancho]:<{ancho}}  {cop(f['_signo']):>13}  "
                  f"{f['Categoría']} / {f['Subcategoría']}{nota}{'  ← REVISAR' if f['_revisar'] else ''}")

    def totales(self, filas):
        t = lambda cat: sum((f["Monto (COP)"] for f in filas if f["Categoría"] == cat), Decimal(0))
        return (f"Ingresos {cop(t('Ingreso'))} · Gasto fijo {cop(t('Gasto Fijo'))} · "
                f"Gasto variable {cop(t('Gasto Variable'))}")

    def csv_fila(self, f, ext, pdf: Path):
        return {"Fecha": f["Fecha"].isoformat(), "Categoria": f["Categoría"], "Subcategoria": f["Subcategoría"],
                "Descripcion": f["Descripción"], "Monto": f["Monto (COP)"], "MontoConSigno": f["_signo"],
                "Sentido": "Salida" if f["_signo"] < 0 else "Entrada", "Cuenta": f["Cuenta"],
                "Periodo": ext.desde.strftime("%Y-%m"), "Archivo": pdf.name,
                "OrigenClasificacion": f["_origen"], "Revisar": "Sí" if f["_revisar"] else ""}
