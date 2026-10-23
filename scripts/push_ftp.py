#!/usr/bin/env python3
"""Sube una canción (ZIP o carpeta) a la Switch por FTP (puerto 5000).

Uso: push_ftp.py <zip|carpeta> <ip-switch>
"""
import ftplib
import os
import sys
import tempfile
import zipfile

REMOTE = "/switch/switch-hero/songs"


def collect(src: str):
    """-> (carpeta local, nombre de carpeta remota)."""
    if os.path.isdir(src):
        return os.path.abspath(src), os.path.basename(os.path.abspath(src))
    if not zipfile.is_zipfile(src):
        sys.exit(f"No es un ZIP ni una carpeta: {src}")
    tmp = tempfile.mkdtemp(prefix="shpush-")
    with zipfile.ZipFile(src) as z:
        for m in z.namelist():                     # evita rutas fuera del directorio
            dest = os.path.realpath(os.path.join(tmp, m))
            if not dest.startswith(os.path.realpath(tmp) + os.sep):
                sys.exit(f"Entrada insegura en el ZIP: {m}")
        z.extractall(tmp)
    entries = [e for e in os.listdir(tmp) if not e.startswith(".")]
    if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
        return os.path.join(tmp, entries[0]), entries[0]
    return tmp, os.path.splitext(os.path.basename(src))[0]


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, host = sys.argv[1:]
    local, name = collect(src)
    ftp = ftplib.FTP()
    ftp.connect(host, 5000, timeout=30)
    ftp.login()
    ftp.set_pasv(True)
    made = set()

    def mkd(path):
        cur = ""
        for part in path.strip("/").split("/"):
            cur += "/" + part
            if cur not in made:
                try:
                    ftp.mkd(cur)
                except ftplib.error_perm:
                    pass
                made.add(cur)

    files = [os.path.join(d, f) for d, _, fs in os.walk(local) for f in fs]
    total = sum(os.path.getsize(f) for f in files) or 1
    done = 0
    for f in files:
        rel = os.path.relpath(f, local).replace(os.sep, "/")
        dst = f"{REMOTE}/{name}/{rel}"
        mkd(os.path.dirname(dst))
        with open(f, "rb") as fh:
            ftp.storbinary("STOR " + dst, fh, 1 << 16)
        done += os.path.getsize(f)
        print(f"{done * 100 // total}% {dst}", flush=True)
    ftp.quit()


if __name__ == "__main__":
    main()
