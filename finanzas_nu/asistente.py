"""Modo guiado: lo que pasa al hacer doble clic en importar.cmd.

1. Pregunta qué PDF importar (ventana de Windows; se pueden elegir varios, de cuenta y de tarjeta).
2. Usa el libro de LIBRO_RUTA (.env); si no está, pregunta cuál es y lo recuerda.
3. Si algún PDF tiene contraseña, la pide (y opcionalmente la recuerda en .env).
4. Muestra la vista previa y pregunta si añadir los movimientos nuevos al libro.
5. Espera a que pulses Enter antes de cerrar la ventana.
Todo queda en registros/registro_importaciones.txt (menos la contraseña).
"""
from __future__ import annotations

import getpass
import os
import re
import traceback
from pathlib import Path

from .comun import registro
from .comun.pdf import problema_de_clave

LINEA = "=" * 70


def _rutas_de_dialogo(titulo: str, tipos, carpeta: Path, varios: bool):
    import tkinter as tk
    from tkinter import filedialog
    raiz = tk.Tk()
    raiz.withdraw()
    raiz.attributes("-topmost", True)  # que la ventana salga delante de la consola
    try:
        if varios:
            return [Path(p) for p in filedialog.askopenfilenames(title=titulo, filetypes=tipos, initialdir=carpeta)]
        p = filedialog.askopenfilename(title=titulo, filetypes=tipos, initialdir=carpeta)
        return Path(p) if p else None
    finally:
        raiz.destroy()


def _guardar_en_env(raiz: Path, clave: str, valor: str):
    env = raiz / ".env"
    lineas = env.read_text(encoding="utf-8-sig").splitlines() if env.exists() else []
    nueva = f"{clave}={valor}"
    for i, l in enumerate(lineas):
        if re.match(rf"\s*{clave}\s*=", l):
            lineas[i] = nueva
            break
    else:
        lineas.append(nueva)
    env.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    os.environ[clave] = valor


def _si(pregunta: str) -> bool:
    while True:
        r = input(f"{pregunta} (s/n): ").strip().lower()
        registro.anotar(f"   [respuesta: {r}]")
        if r in ("s", "si", "sí"):
            return True
        if r in ("n", "no"):
            return False


def asistente() -> int:
    from .__main__ import RAIZ, RESULTADO, main
    from .comun.proceso import LIBRO_ABIERTO
    try:
        print(LINEA)
        print("  IMPORTAR EXTRACTOS NU (CUENTA Y TARJETA DE CRÉDITO)")
        print(LINEA)

        # 1. PDF
        print("\nPaso 1 de 4 · Elige el PDF o los PDF (se abre una ventana; con Ctrl puedes marcar varios).")
        descargas = Path.home() / "Downloads"
        pdfs = _rutas_de_dialogo("Elige los extractos de Nu (PDF)", [("PDF", "*.pdf")],
                                 descargas if descargas.exists() else RAIZ, varios=True)
        if not pdfs:
            print("No elegiste ningún PDF. No se hizo nada.")
            return 0
        for p in pdfs:
            print(f"   · {p}")

        # 2. Libro
        libro = os.environ.get("LIBRO_RUTA")
        if libro and Path(libro).exists():
            print(f"\nPaso 2 de 4 · Libro: {libro}")
        else:
            print("\nPaso 2 de 4 · Elige tu libro financiero de Excel (se abre una ventana).")
            elegido = _rutas_de_dialogo("Elige el libro financiero", [("Excel", "*.xlsx")],
                                        Path.home() / "Documents", varios=False)
            if not elegido:
                print("No elegiste el libro. No se hizo nada.")
                return 0
            libro = str(elegido)
            _guardar_en_env(RAIZ, "LIBRO_RUTA", libro)
            print(f"   · {libro}  (guardado para la próxima vez)")

        # 3. Contraseña de los PDF, si hace falta
        print("\nPaso 3 de 4 · Comprobando si los PDF tienen contraseña…")
        for p in pdfs:
            motivo = problema_de_clave(p, os.environ.get("NU_PDF_CLAVE"))
            intentos = 0
            while motivo and intentos < 3:
                print(f"   {p.name} tiene contraseña (la que te da Nu para abrir el extracto).")
                clave = getpass.getpass("   Escríbela y pulsa Enter (no se ve mientras escribes): ")
                motivo = problema_de_clave(p, clave)
                intentos += 1
                if motivo:
                    print("   Esa contraseña no abre el PDF.")
                else:
                    os.environ["NU_PDF_CLAVE"] = clave
                    if _si("   ¿Guardarla en este computador para no volver a pedirla?"):
                        _guardar_en_env(RAIZ, "NU_PDF_CLAVE", clave)
            if motivo:
                print(f"   No se pudo abrir {p.name}. No se hizo nada.")
                return 1
        print("   OK")

        # 4. Vista previa y confirmación
        print("\nPaso 4 de 4 · Vista previa (todavía no se ha tocado el Excel):")
        main([str(p) for p in pdfs] + ["--libro", libro])
        print("\n" + LINEA)
        if not RESULTADO.total_pendientes:
            print("No hay movimientos nuevos: todo lo de estos PDF ya está en el libro.")
            return 0
        from .__main__ import IMPORTADORES
        for nombre, filas in RESULTADO.pendientes.items():
            print(f"Hay {len(filas)} movimientos nuevos para la hoja '{IMPORTADORES[nombre].hoja}'.")
        if RESULTADO.errores:
            print("OJO: algún PDF falló o no cuadró y se deja fuera (ver mensajes con ✗ arriba).")
        if not _si("¿Añadirlos al libro ahora?"):
            print("No se añadió nada. Los CSV quedaron en la carpeta 'salida'.")
            return 0
        while True:
            codigo = main([str(p) for p in pdfs] + ["--libro", libro, "--escribir", "--sin-tabla"])
            if codigo != LIBRO_ABIERTO:
                return codigo
            input("Cierra el libro en Excel (guárdalo si tenías cambios) y pulsa Enter para reintentar… ")
            registro.anotar("   [reintento tras cerrar Excel]")
    except KeyboardInterrupt:
        print("\nCancelado.")
        return 1
    except Exception:
        print("\nAlgo falló. Copia este mensaje para revisarlo:\n")
        traceback.print_exc()
        return 1
    finally:
        if registro.ruta():
            print(f"\nTodo lo anterior quedó guardado en: {registro.ruta()}")
        try:
            input("\nPulsa Enter para cerrar esta ventana… ")
        except EOFError:
            pass
