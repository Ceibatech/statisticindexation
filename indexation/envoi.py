"""Envoi via l'API Resend (POST https://api.resend.com/emails), images en ligne par content_id."""
from __future__ import annotations

import base64
import logging
import os
import time

import requests

log = logging.getLogger(__name__)


def envoyer(cfg_mail: dict, sujet: str, html: str, texte: str, images: dict[str, bytes],
            idempotence: str, destinataires: list[str] | None = None,
            fichiers: dict[str, bytes] | None = None) -> str:
    rc = cfg_mail.get("resend", {})
    cle = os.environ.get("RESEND_API_KEY")
    if not cle:
        raise RuntimeError("RESEND_API_KEY absent (.env)")
    to = destinataires or cfg_mail["destinataires"]
    rep = cfg_mail.get("repondre_a")
    reply_to = cfg_mail["destinataires"] if rep in (None, "destinataires") else ([rep] if isinstance(rep, str) else rep)
    corps = {
        "from": cfg_mail["expediteur"], "to": to, "subject": sujet, "html": html, "text": texte,
        "reply_to": reply_to,
        "attachments": [{"filename": f"{cid}.png", "content": base64.b64encode(b).decode(), "content_id": cid}
                        for cid, b in images.items()]
                       + [{"filename": nom, "content": base64.b64encode(b).decode()} for nom, b in (fichiers or {}).items()],
        "tags": [{"name": "flux", "value": "indexation_sae"}],
    }
    if cfg_mail.get("copie"):
        corps["cc"] = cfg_mail["copie"]
    entetes = {"Authorization": f"Bearer {cle}", "Content-Type": "application/json", "Idempotency-Key": idempotence}
    tentatives = rc.get("tentatives", 3)
    for i in range(1, tentatives + 1):
        try:
            r = requests.post(rc.get("url", "https://api.resend.com/emails"), json=corps, headers=entetes,
                              timeout=rc.get("timeout_s", 30))
        except requests.RequestException as e:
            if i == tentatives:
                raise
            log.warning("Resend injoignable (%s), nouvel essai", e)
        else:
            if r.status_code < 300:
                return r.json().get("id", "")
            if r.status_code in (429, 500, 502, 503, 504) and i < tentatives:
                log.warning("Resend HTTP %s, nouvel essai", r.status_code)
            else:
                raise RuntimeError(f"Resend HTTP {r.status_code} : {r.text[:400]}")
        time.sleep(2 ** i)
    raise RuntimeError("Envoi Resend impossible")
