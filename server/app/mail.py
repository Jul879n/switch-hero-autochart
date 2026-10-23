"""Envío de correo por SMTP (Stalwart en el VPS). Sin configuración, no envía y lo informa."""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("mail")


def configured() -> bool:
    return all(os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"))


def send(to: str, subject: str, body: str) -> bool:
    """True si se entregó al servidor SMTP; nunca lanza excepciones."""
    if not configured():
        log.warning("SMTP sin configurar; no se envió '%s' a %s", subject, to)
        return False
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "465"))
    user, pw = os.environ["SMTP_USER"], os.environ["SMTP_PASS"]
    m = EmailMessage()
    m["From"] = os.environ.get("SMTP_FROM") or user
    m["To"], m["Subject"] = to, subject
    m.set_content(body)
    try:
        ctx = ssl.create_default_context()
        if port == 465:
            s = smtplib.SMTP_SSL(host, port, context=ctx, timeout=20)
        else:
            s = smtplib.SMTP(host, port, timeout=20)
            s.starttls(context=ctx)
        with s:
            s.login(user, pw)
            s.send_message(m)
        return True
    except Exception:
        log.exception("Falló el envío de correo a %s", to)
        return False
