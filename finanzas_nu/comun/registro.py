"""Registro en texto de cada ejecución: registros/registro_importaciones.txt.

Todo lo que el programa muestra en pantalla (y las respuestas s/n del modo guiado)
se copia al final de ese archivo, con fecha y hora. Las contraseñas nunca se escriben.
"""
from __future__ import annotations

import getpass
import sys
from datetime import datetime
from pathlib import Path

_archivo = None


class _Doble:
    """Escribe a la vez en la consola y en el registro."""

    def __init__(self, consola, archivo):
        self.consola, self.archivo = consola, archivo

    def write(self, s):
        self.consola.write(s)
        try:
            self.archivo.write(s)
            self.archivo.flush()
        except Exception:
            pass
        return len(s)

    def flush(self):
        self.consola.flush()

    def __getattr__(self, nombre):
        return getattr(self.consola, nombre)


def iniciar(carpeta: Path, argumentos: list[str]):
    global _archivo
    if _archivo is not None:
        return
    carpeta.mkdir(parents=True, exist_ok=True)
    _archivo = open(carpeta / "registro_importaciones.txt", "a", encoding="utf-8")
    try:
        usuario = getpass.getuser()
    except Exception:
        usuario = "?"
    _archivo.write("\n" + "#" * 100 + "\n"
                   f"# {datetime.now():%Y-%m-%d %H:%M:%S} · usuario {usuario} · "
                   f"{'modo guiado (doble clic)' if not argumentos else 'consola: ' + ' '.join(argumentos)}\n"
                   + "#" * 100 + "\n")
    _archivo.flush()
    sys.stdout = _Doble(sys.stdout, _archivo)
    sys.stderr = _Doble(sys.stderr, _archivo)


def anotar(texto: str):
    """Sólo al registro (p. ej. la respuesta que escribió el usuario)."""
    if _archivo is not None:
        _archivo.write(texto + "\n")
        _archivo.flush()


def ruta() -> str | None:
    return _archivo.name if _archivo is not None else None
