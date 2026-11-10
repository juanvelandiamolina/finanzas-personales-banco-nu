"""Lee el extracto mensual de la Cuenta Nu (ahorros) y devuelve sus movimientos.

Cada movimiento es una línea con tres columnas a x fija: fecha "01 oct" (x≈68),
descripción (x≈121) y monto "-$6.000,00" / "+$125.000,00" (x≈450-480). El
rendimiento del mes va al final, sin fecha, como "Rendimiento total de tu cuenta".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from ..comun.pdf import NOMBRE_MES, Linea, leer_lineas, mes, monto_cop

X_DESCRIPCION = 121  # columna de la descripción en las páginas de movimientos
X_RESUMEN = 290      # a la derecha de esto están los valores del resumen de la página 1


@dataclass
class Movimiento:
    fecha: date
    descripcion: str
    monto: Decimal              # con signo: negativo = sale dinero
    pagina: int | None = None
    es_rendimiento: bool = False


@dataclass
class ExtractoCuenta:
    archivo: Path
    placa: str | None
    desde: date
    hasta: date
    periodo_texto: str
    resumen: dict[str, Decimal] = field(default_factory=dict)
    movimientos: list[Movimiento] = field(default_factory=list)


def leer_extracto_cuenta(ruta: str | Path, clave: str | None = None) -> ExtractoCuenta:
    ruta = Path(ruta)
    paginas = leer_lineas(ruta, clave)

    # ---- Cabecera: placa y período "01 - 31 OCT 2026"
    todas = [l for p in paginas for l in p]
    ext = None
    for i, l in enumerate(todas):
        m = re.search(r"(\d{1,2})\s*-\s*(\d{1,2})\s+([A-Z]{3})\s+(\d{4})", l.texto, re.I)
        if m and i > 0 and re.search(r"Per[ií]odo", todas[i - 1].texto, re.I):
            n_mes, anio = mes(m.group(3)), int(m.group(4))
            ext = ExtractoCuenta(archivo=ruta, placa=l.trozos[0][1], periodo_texto=m.group(0),
                                 desde=date(anio, n_mes, int(m.group(1))), hasta=date(anio, n_mes, int(m.group(2))))
            break
    if ext is None:
        raise ValueError(f'"{ruta.name}" no parece un extracto mensual de Cuenta Nu (no encontré el Período).')

    # ---- Resumen de la página 1 (sirve para cuadrar)
    etiquetas = {
        "inicio": r"Tu dinero al inicio del mes", "entro": r"Lo que entró a tu cuenta",
        "salio": r"Lo que salió de tu cuenta", "impuesto": r"Impuesto del 4x1000",
        "costos": r"Costos por retiro", "rendimiento": r"Rendimiento total de tu cuenta",
        "fin": r"Tu dinero a final del mes",
    }
    for l in paginas[0]:
        for k, patron in etiquetas.items():
            if k in ext.resumen or not re.search(patron, l.texto, re.I):
                continue
            valores = [v for v in (monto_cop(s) for x, s in l.trozos if x > X_RESUMEN and "$" in s) if v is not None]
            if valores:
                ext.resumen[k] = abs(valores[-1])

    # ---- Movimientos (páginas con el título "Movimientos")
    rendimiento_mov = None
    for lineas_pag in paginas:
        if not any(l.texto.strip().lower() == "movimientos" for l in lineas_pag):
            continue
        ultimo: tuple[Movimiento, float] | None = None
        for l in lineas_pag:
            tz = l.trozos
            monto = monto_cop(tz[-1][1]) if len(tz) >= 2 else None
            fm = re.fullmatch(r"(\d{1,2})\s+([a-záéíóú]{3})\.?", tz[0][1], re.I)
            if fm and monto is not None and len(tz) >= 3:
                fecha = _fecha_en_periodo(int(fm.group(1)), mes(fm.group(2)), ext.hasta)
                desc = re.sub(r"\s+", " ", " ".join(s for _, s in tz[1:-1])).strip()
                mov = Movimiento(fecha, desc, monto, pagina=l.pagina)
                ext.movimientos.append(mov)
                ultimo = (mov, l.top)
            elif re.search(r"Rendimiento total de tu cuenta", l.texto, re.I) and monto is not None:
                rendimiento_mov = monto
                ultimo = None
            elif (ultimo and len(tz) == 1 and abs(tz[0][0] - X_DESCRIPCION) < 6
                  and l.top - ultimo[1] < 20 and not re.search(r"rendimientos se pagan", tz[0][1], re.I)):
                # descripción partida en dos líneas: la segunda va pegada debajo, sin fecha ni monto
                ultimo[0].descripcion += " " + tz[0][1]
            else:
                ultimo = None

    rendimiento = rendimiento_mov if rendimiento_mov is not None else ext.resumen.get("rendimiento")
    if rendimiento:
        ext.movimientos.append(Movimiento(
            fecha=ext.hasta,
            descripcion=f"Rendimientos Nu {NOMBRE_MES[ext.hasta.month - 1]} {ext.hasta.year}",
            monto=abs(rendimiento), es_rendimiento=True))
    return ext


def cuadrar(ext: ExtractoCuenta) -> list[str]:
    """Comprueba que los movimientos leídos explican el saldo del resumen. Lista vacía = cuadra."""
    r, avisos = ext.resumen, []
    neto = sum((m.monto for m in ext.movimientos), Decimal(0))
    if "inicio" in r and "fin" in r:
        esperado = r["fin"] - r["inicio"]
        if neto != esperado:
            avisos.append(f"El neto de los movimientos ({neto}) no explica el saldo: fin - inicio = {esperado}. "
                          f"Diferencia {esperado - neto}.")
    else:
        avisos.append("No encontré saldo inicial/final en el resumen; no pude cuadrar.")
    entro = sum((m.monto for m in ext.movimientos if m.monto > 0 and not m.es_rendimiento), Decimal(0))
    if "entro" in r and entro != r["entro"]:
        avisos.append(f'Entradas leídas {entro} ≠ "Lo que entró" {r["entro"]}.')
    return avisos


def _fecha_en_periodo(dia: int, n_mes: int, hasta: date) -> date:
    # un extracto de enero puede traer un movimiento del 31 de diciembre y viceversa
    anio = hasta.year
    if n_mes == 12 and hasta.month == 1:
        anio -= 1
    elif n_mes == 1 and hasta.month == 12:
        anio += 1
    return date(anio, n_mes, dia)
