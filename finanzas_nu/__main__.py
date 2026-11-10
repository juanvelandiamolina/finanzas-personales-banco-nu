"""Extractos PDF de Nu (Cuenta y Tarjeta de Crédito) → movimientos clasificados → CSV y libro de Excel.

    python -m finanzas_nu                                    # modo guiado (lo que hace el doble clic)
    python -m finanzas_nu extracto.pdf                       # vista previa + CSV (no toca el Excel)
    python -m finanzas_nu carpeta_con_pdfs\\                  # todos los PDF de la carpeta
    python -m finanzas_nu extracto.pdf --escribir            # además los añade al libro
    python -m finanzas_nu extracto.pdf --comparar            # compara con lo que ya hay en el libro

Cada PDF se reconoce solo: los de la Cuenta Nu van a la hoja 'Ingresos y Gastos'
y los de la Tarjeta de Crédito a la hoja 'Tarjeta de Crédito' (se crea si no existe).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from .comun import registro
from .comun.pdf import tipo_de_extracto
from .comun.proceso import LIBRO_ABIERTO, Resultado, escribir, procesar
from .cuenta.importar import ImportadorCuenta
from .tarjeta.importar import ImportadorTarjeta

RAIZ = Path(__file__).resolve().parent.parent
IMPORTADORES = {i.nombre: i for i in (ImportadorCuenta(), ImportadorTarjeta())}
RESULTADO = Resultado()      # resumen de la última ejecución (lo usa el asistente)


def cargar_env(ruta: Path):
    """.env mínimo (CLAVE=valor), sin pisar variables ya definidas."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", linea)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = re.sub(r"^(['\"])(.*)\1$", r"\2", m.group(2))


def expandir(entradas: list[str]) -> list[Path]:
    out = []
    for e in map(Path, entradas):
        out += sorted(p for p in e.rglob("*") if p.suffix.lower() == ".pdf") if e.is_dir() else [e]
    return out


def preparar_consola():
    for flujo in (sys.stdout, sys.stderr):  # tildes y ✓ en la consola de Windows
        try:
            flujo.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


def main(argv=None) -> int:
    preparar_consola()
    cargar_env(RAIZ / ".env")
    if argv is None:
        registro.iniciar(RAIZ / "registros", sys.argv[1:])
        if len(sys.argv) == 1:  # doble clic en importar.cmd: modo guiado
            from .asistente import asistente
            return asistente()

    ap = argparse.ArgumentParser(prog="python -m finanzas_nu", description=__doc__.split("\n")[0])
    ap.add_argument("pdfs", nargs="+", help="PDF del extracto o carpeta con PDF")
    ap.add_argument("--libro", default=os.environ.get("LIBRO_RUTA"),
                    help="libro de Excel (o LIBRO_RUTA en .env)")
    ap.add_argument("--escribir", action="store_true", help="añade al libro lo que aún no esté (con respaldo)")
    ap.add_argument("--comparar", action="store_true", help="compara el PDF con el libro (no escribe)")
    ap.add_argument("--forzar", action="store_true", help="escribe aunque el PDF no cuadre con su resumen")
    ap.add_argument("--sin-historial", action="store_true", help="clasifica sólo con las reglas de config/")
    ap.add_argument("--sin-tabla", action="store_true", help="no lista cada movimiento, sólo el resumen")
    ap.add_argument("--cuenta", default=os.environ.get("CUENTA", "Nu"), help='columna Cuenta (por defecto "Nu")')
    ap.add_argument("--salida", default=str(RAIZ / "salida"), help="carpeta de los CSV")
    a = ap.parse_args(argv)
    if (a.escribir or a.comparar) and not a.libro:
        ap.error("para --escribir o --comparar hace falta --libro <ruta.xlsx> o LIBRO_RUTA en .env")
    if a.libro and not Path(a.libro).exists():
        print(f"No existe el libro: {a.libro}", file=sys.stderr)
        return 1
    a.clave = os.environ.get("NU_PDF_CLAVE")
    if a.libro:
        print(f"Libro: {a.libro}")

    pdfs = expandir(a.pdfs)
    res = procesar(pdfs, IMPORTADORES, lambda p: tipo_de_extracto(p, a.clave), a, RAIZ)
    RESULTADO.pendientes, RESULTADO.errores = res.pendientes, res.errores
    codigo = 1 if res.errores else 0
    if a.escribir:
        c = escribir(res, IMPORTADORES, a.libro, RAIZ)
        if c == LIBRO_ABIERTO:
            return c
    if argv is None and registro.ruta():
        print(f"\nRegistro de esta ejecución: {registro.ruta()}")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
