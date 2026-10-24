"""Envío de una carta a la Switch por FTP (homebrew ftpd, sin login, puerto 5000 por defecto)."""
from __future__ import annotations

import ftplib
import ipaddress
from pathlib import Path

REMOTE = "/switch/switch-hero/songs"


class FtpError(Exception):
    pass


def check_host(host: str, port: int) -> str:
    """Acepta cualquier IP (LAN, Tailscale, pública); excluye las que apuntan al propio servidor o a su metadata."""
    try:
        ip = ipaddress.ip_address(host.strip().replace(",", ".").replace(" ", ""))   # teclados móviles ponen «,»
    except ValueError:
        raise FtpError("Ingrese la IP de la Switch (por ejemplo 192.168.1.50)")
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        raise FtpError("IP no permitida (loopback, link-local o multicast)")
    if not 1 <= port <= 65535:
        raise FtpError("Puerto inválido")
    return str(ip)


def check(host: str, port: int, timeout: float = 8) -> str:
    """Conecta y abre sesión; devuelve la IP validada o lanza FtpError."""
    host = check_host(host, port)
    ftp = ftplib.FTP()
    try:
        ftp.connect(host, port, timeout=timeout)
        ftp.login()
        ftp.quit()
    except (OSError, ftplib.Error) as e:
        raise FtpError(f"No se pudo conectar a {host}:{port}: {e}. El servidor debe poder alcanzar la Switch "
                       "(misma red o VPN) con el FTP (ftpd) abierto.") from e
    finally:
        try:
            ftp.close()
        except Exception:
            pass
    return host


def push(host: str, port: int, folder: Path, name: str, timeout: float = 15) -> int:
    """Sube los archivos de `folder` a REMOTE/name. Devuelve la cantidad de archivos."""
    host = check_host(host, port)
    files = [f for f in sorted(folder.iterdir()) if f.is_file()]
    if not files:
        raise FtpError("La carta no tiene archivos")
    ftp = ftplib.FTP()
    try:
        ftp.connect(host, port, timeout=timeout)
        ftp.login()
        ftp.set_pasv(True)
        cur = ""
        for part in f"{REMOTE}/{name}".strip("/").split("/"):
            cur += "/" + part
            try:
                ftp.mkd(cur)
            except ftplib.error_perm:
                pass                                   # ya existe
        for f in files:
            with open(f, "rb") as fh:
                ftp.storbinary(f"STOR {REMOTE}/{name}/{f.name}", fh, 1 << 16)
        ftp.quit()
    except (OSError, ftplib.Error) as e:
        raise FtpError(f"No se pudo enviar a {host}:{port}: {e}. El servidor debe poder alcanzar la Switch "
                       "(misma red o VPN) con el FTP (ftpd) abierto.") from e
    finally:
        try:
            ftp.close()
        except Exception:
            pass
    return len(files)
