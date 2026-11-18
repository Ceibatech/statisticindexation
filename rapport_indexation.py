#!/usr/bin/env python3
"""Suivi de l'indexation dans le SAE MULCV — CEIBA Analytics.

    python rapport_indexation.py midi        # point de mi-journée (lun.–ven. 12h30)
    python rapport_indexation.py jour        # point du jour (lun.–ven. 18h00)
    python rapport_indexation.py hebdo       # récapitulatif de la semaine (vendredi 18h20)
    options :
      --apercu               n'envoie rien : écrit out/<rapport>.html, les images et le classeur Excel
      --a ADRESSE            envoie seulement à ADRESSE (test ; plusieurs séparées par des virgules)
      --forcer               renvoie même si ce rapport a déjà été envoyé
      --demo                 données simulées (signalées dans le rapport)
      --maintenant "2026-11-17 18:05"   rejoue un créneau

Une collecte en échec n'envoie PAS de rapport faux : une alerte technique part seule vers email.alerte_technique.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import traceback
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

RACINE = Path(__file__).resolve().parent
sys.path.insert(0, str(RACINE))

from indexation import envoi, exports, indicateurs, rendu, sources  # noqa: E402
from indexation.store import Store  # noqa: E402

log = logging.getLogger("indexation")


def charger_env(chemin: Path):
    if chemin.exists():
        for ligne in chemin.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if ligne and not ligne.startswith("#") and "=" in ligne:
                k, v = ligne.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("type", choices=["midi", "jour", "hebdo"])
    ap.add_argument("--config", default=str(RACINE / "config.yaml"))
    ap.add_argument("--apercu", action="store_true")
    ap.add_argument("--a", dest="test_dest")
    ap.add_argument("--forcer", action="store_true")
    ap.add_argument("--maintenant")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--rectificatif")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    charger_env(RACINE / ".env")
    cfg = yaml.safe_load(open(a.config, encoding="utf-8"))
    if a.demo:
        cfg["source"]["type"] = "demo"
        cfg["stockage"]["base_sqlite"] = "data/demo.db"
    tz = ZoneInfo(cfg["projet"]["fuseau_horaire"])
    maintenant = datetime.fromisoformat(a.maintenant) if a.maintenant else datetime.now(tz).replace(tzinfo=None)
    c = cfg["calendrier"]
    cal = indicateurs.Calendrier(c["jours_ouvres"], {datetime.fromisoformat(d).date() for d in c.get("jours_feries", [])},
                                 time.fromisoformat(c["heure_debut"]), time.fromisoformat(c["heure_fin"]))
    if not cal.ouvre(maintenant.date()) and not (a.apercu or a.test_dest):
        log.info("Jour non ouvré (%s) : aucun rapport.", maintenant.date())
        return 0
    store = Store(str(RACINE / cfg["stockage"]["base_sqlite"]))
    cle = f"{a.type}-{maintenant:%Y-%m-%d}"
    if store.deja_envoye(cle) and not (a.forcer or a.apercu or a.test_dest):
        log.info("Rapport %s déjà envoyé (--forcer pour renvoyer).", cle)
        return 0

    # 1. Collecte
    try:
        col = sources.collecter(cfg, tz, maintenant)
        if not col.pieces:
            raise RuntimeError("Aucune pièce trouvée sous le dossier Scan ni dans le journal du service Scan : "
                               "périmètre erroné ou lecture impossible.")
        store.noter_collecte(col.mode, len(col.pieces), len(col.evenements))
        log.info("Collecte (%s) : %s pièces, %s événements, %s utilisateurs", col.mode, len(col.pieces),
                 len(col.evenements), len(col.personnes))
    except Exception as e:  # pas de rapport faux : alerte technique seulement
        log.error("Collecte en échec : %s", e)
        if not a.apercu:
            try:
                envoi.envoyer(cfg["email"], f"⚠ [Indexation SAE] Rapport {a.type} non envoyé — collecte en échec",
                              f"<p>La lecture du SAE a échoué à {maintenant:%H:%M} ; le rapport n'a pas été diffusé.</p>"
                              f"<pre>{traceback.format_exc()[-2500:]}</pre>", traceback.format_exc()[-2500:], {},
                              f"alerte-indexation-{cle}-{maintenant:%H%M}", cfg["email"]["alerte_technique"])
            except Exception as e2:  # noqa: BLE001
                log.error("Alerte technique non envoyée : %s", e2)
        return 2

    # 2. Indicateurs
    P, E, V = indicateurs.preparer(col.pieces, col.evenements, col.personnes, cfg)
    r = indicateurs.construire(P, E, V, cfg, cal, maintenant, a.type, col.personnes, col.mode)
    r.source = {"journal": "SAE MULCV — journal du service Scan + base Alfresco (lecture seule)",
                "degrade": "SAE MULCV — base Alfresco seule (mode dégradé)",
                "demo": "DONNÉES DE DÉMONSTRATION"}[col.mode]

    # 3. Rendu
    nom_xlsx = f"Indexation-SAE-{'semaine' if a.type == 'hebdo' else 'jour'}-{maintenant:%Y-%m-%d}.xlsx"
    sujet, html, texte, images, ctx = rendu.rendre(r, cfg, cal, a.rectificatif,
                                                   f"{nom_xlsx} (détail par indexeur, par jour et par pièce)")
    fichiers = {}
    try:
        fichiers[nom_xlsx] = exports.classeur(r, cfg, ctx)
    except Exception as e:  # noqa: BLE001 — une pièce jointe en échec n'empêche pas le rapport
        log.exception("Excel non produit : %s", e)
    sortie = RACINE / cfg["stockage"]["dossier_sortie"]
    sortie.mkdir(exist_ok=True)
    apercu = html
    for cid, b in images.items():
        (sortie / f"{cle}-{cid}.png").write_bytes(b)
        apercu = apercu.replace(f"cid:{cid}", f"{cle}-{cid}.png")
    (sortie / f"{cle}.html").write_text(apercu, encoding="utf-8")
    for f, b in fichiers.items():
        (sortie / f).write_bytes(b)
    log.info("Sujet : %s", sujet)
    if a.apercu:
        log.info("Aperçu écrit : %s", sortie / f"{cle}.html")
        return 0

    # 4. Envoi
    dest = [x.strip() for x in a.test_dest.split(",") if x.strip()] if a.test_dest and a.test_dest.lower() != "tous" else None
    idem = f"indexation-{cle}"
    if a.test_dest or a.forcer or a.rectificatif:
        idem += "-" + hashlib.sha1(f"{a.test_dest}|{a.rectificatif}|{datetime.now().isoformat()}".encode()).hexdigest()[:16]
    rid = envoi.envoyer(cfg["email"], sujet, html, texte, images, idem, dest, fichiers)
    if not a.test_dest:
        store.noter_envoi(cle, rid, sujet)
    log.info("Envoyé (Resend id %s)", rid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
