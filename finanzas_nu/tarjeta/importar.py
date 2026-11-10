"""Tarjeta de Crédito Nu → hoja 'Tarjeta de Crédito', tabla tblTarjeta.

Grano: una fila por línea del extracto. Una compra a 24 cuotas aparece en 24 extractos
(cuota 1 de 24, 2 de 24...), una vez en cada uno, con lo que se cobró ese mes.
  - Gasto real del mes (lo que compraste): Tipo = Compra y "Nueva en el Extracto" = Sí → columna Valor.
  - Lo que te cobraron ese mes: columna "Total a Pagar" (+ Comisiones).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from ..comun.clasificar import clasificar
from ..comun.libro_excel import Columna
from ..comun.proceso import Importador, clave_texto, cop
from .extracto import cuadrar, leer_extracto_tarjeta

T = "tblTarjeta"


def _es_nueva(m) -> bool:
    """¿Aparece por primera vez en este extracto? Pagos, devoluciones y ajustes siempre son del mes.
    Una compra es nueva si va en su cuota 1 y se cobra capital este mes (una compra que sólo genera
    intereses o va en la cuota 2, 3... ya salió en un extracto anterior). No se usa la fecha porque
    una compra del último día del periodo puede llegar en el extracto siguiente."""
    if m.tipo not in ("Compra", "Avance"):
        return True
    return (m.cuota in (None, 1)) and m.valor_mes > 0


def _f(expr: str) -> str:
    return f'IF({T}[[#This Row],[Fecha]]="","",{expr})'


class ImportadorTarjeta(Importador):
    nombre = "tarjeta"
    titulo = "Tarjeta de Crédito Nu"
    hoja = "Tarjeta de Crédito"
    tabla = T
    archivo_reglas = "reglas_tarjeta.json"

    def columnas(self):
        return [
            Columna("Fecha", "fecha", ancho=12),
            Columna("Categoría", ancho=16),
            Columna("Subcategoría", ancho=16),
            Columna("Descripción", ancho=32),
            Columna("Tipo", ancho=14),
            Columna("Valor (COP)", "moneda", ancho=16),
            Columna("Cuota", "entero", ancho=8),
            Columna("Total Cuotas", "entero", ancho=8),
            Columna("Valor del Mes", "moneda", ancho=16),
            Columna("Tasa M.V.", "porcentaje", ancho=10),
            Columna("Intereses", "moneda", ancho=14),
            Columna("Comisiones", "moneda", ancho=13),
            Columna("Total a Pagar", "moneda", ancho=16),
            Columna("Restante", "moneda", ancho=16),
            Columna("Nueva en el Extracto", ancho=12),
            Columna("Fecha de Corte", "fecha", ancho=13),
            Columna("Año", formula=_f(f"YEAR({T}[[#This Row],[Fecha]])"), ancho=8),
            Columna("Mes", formula=_f(f"MONTH({T}[[#This Row],[Fecha]])"), ancho=6),
            Columna("AAAA-MM", formula=_f(f'YEAR({T}[[#This Row],[Fecha]])&"-"&TEXT(MONTH({T}[[#This Row],[Fecha]]),"00")'), ancho=10),
            Columna("Tarjeta", ancho=12),
        ]

    def leer(self, pdf, clave):
        return leer_extracto_tarjeta(pdf, clave)

    def cuadrar(self, ext):
        return cuadrar(ext)

    def periodo(self, ext):
        return ext.desde, ext.hasta, f"extracto de {ext.mes_extracto} · {ext.periodo_texto}"

    def filas(self, ext, reglas, historial, opciones):
        out = []
        for i, m in enumerate(ext.movimientos):
            signo = -m.valor if m.tipo in ("Compra", "Avance") else m.valor
            c = clasificar(m.descripcion, signo, reglas, historial, tipo=m.tipo)
            out.append({
                "Fecha": m.fecha, "Categoría": c.categoria, "Subcategoría": c.subcategoria,
                "Descripción": m.descripcion, "Tipo": m.tipo, "Valor (COP)": m.valor,
                "Cuota": m.cuota, "Total Cuotas": m.cuotas, "Valor del Mes": m.valor_mes,
                "Tasa M.V.": m.tasa, "Intereses": m.intereses, "Comisiones": m.comisiones,
                "Total a Pagar": m.total, "Restante": m.restante,
                "Nueva en el Extracto": "Sí" if _es_nueva(m) else "No",
                "Fecha de Corte": ext.corte, "Tarjeta": f"Nu ****{ext.tarjeta}" if ext.tarjeta else "Nu",
                "_origen": c.origen, "_revisar": c.revisar, "_orden": (ext.corte, m.fecha, i),
            })
        return out

    def clave(self, f):
        return clave_texto(f.get("Fecha de Corte"), f.get("Fecha"), f.get("Descripción"),
                           f.get("Valor (COP)") or 0, f.get("Cuota") or 0, f.get("Total a Pagar") or 0)

    def imprimir(self, filas):
        for f in filas:
            cuota = f"{f['Cuota']}/{f['Total Cuotas']}" if f["Cuota"] else ""
            nota = " (historial)" if f["_origen"] == "historial" else ""
            vieja = "" if f["Nueva en el Extracto"] == "Sí" else " ·anterior"
            print(f"  {f['Fecha']}  {f['Descripción'][:26]:<26} {f['Tipo'][:10]:<10} {cop(f['Valor (COP)']):>13} "
                  f"{cuota:>5}  cobro {cop(f['Total a Pagar']):>12}  {f['Categoría']} / {f['Subcategoría']}"
                  f"{nota}{vieja}{'  ← REVISAR' if f['_revisar'] else ''}")

    def totales(self, filas):
        S = lambda xs: sum(xs, Decimal(0))
        nuevas = S(f["Valor (COP)"] for f in filas if f["Tipo"] == "Compra" and f["Nueva en el Extracto"] == "Sí")
        cobro = S(f["Total a Pagar"] + f["Comisiones"] for f in filas)
        pagos = S(f["Valor (COP)"] for f in filas if f["Tipo"] == "Pago")
        return f"Compras nuevas {cop(nuevas)} · Cobro del mes {cop(cobro)} · Pagos hechos {cop(pagos)}"

    def csv_fila(self, f, ext, pdf: Path):
        out = {k.replace("í", "i").replace("ó", "o"): v for k, v in f.items() if not k.startswith("_")}
        out["Fecha"] = f["Fecha"].isoformat()
        out["Fecha de Corte"] = f["Fecha de Corte"].isoformat()
        out["Archivo"], out["OrigenClasificacion"], out["Revisar"] = pdf.name, f["_origen"], "Sí" if f["_revisar"] else ""
        return out
