#!/usr/bin/env python3
"""Qui a touché les pièces un jour donné, source par source (diagnostic des indexeurs manquants).

    sudo .venv/bin/python outils/qui.py 2026-11-18

Affiche, pour la journée :
  1. Alfresco — dernière modification des pièces (cm:modifier), par compte
  2. Alfresco — versions créées (historique), par compte
  3. Journal du service Scan — événements par type et par compte
  4. Ce que le rapport retient comme indexeurs, et les comptes écartés comme techniques
"""
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
from indexation import indicateurs as I, sources  # noqa: E402

cfg = yaml.safe_load(open(RACINE / "config.yaml", encoding="utf-8"))
tz = ZoneInfo(cfg["projet"]["fuseau_horaire"])
jour = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(tz).date()
tech = re.compile(cfg["perimetre"].get("comptes_techniques") or r"^$")


def afficher(titre, compteur):
    print(f"\n=== {titre}")
    if not compteur:
        print("  (rien)")
    for k, n in compteur.most_common():
        etiquette = "  [écarté : compte technique]" if isinstance(k, str) and tech.search(k) else ""
        if isinstance(k, tuple):
            etiquette = "  [écarté : compte technique]" if k[1] and tech.search(k[1]) else ""
        print(f"  {n:6}  {k}{etiquette}")


alf = sources.AlfrescoSql(cfg["source"].get("alfresco_sql"))
per = cfg["perimetre"]
racine = per.get("racine_scan_uuid") or alf.racine(per["site"], per["dossier_scan"])
jobs, audit, agent = [], [], []
try:
    jobs, audit, agent = sources.JournalScan(cfg["source"].get("journal_scan") or {}).lire()
except Exception as e:  # noqa: BLE001
    print(f"Journal du service Scan illisible : {e}")
noeuds = alf.noeuds(racine, [j["node_id"] for j in jobs])
print(f"Racine Scan {racine} : {len(noeuds)} pièces Alfresco, {len(jobs)} pièces au journal, {len(audit)} événements, "
      f"{len(agent)} lignes agent")

def le_jour(v):
    d = sources._dt(v, tz)
    return d is not None and d.date() == jour

afficher(f"Alfresco — dernière modification le {jour:%d/%m/%Y} (cm:modifier)",
         Counter(n["modifieur"] for n in noeuds if le_jour(n["modifie_le"])))
versions = alf.versions([n["id"] for n in noeuds])
afficher(f"Alfresco — versions créées le {jour:%d/%m/%Y} (sur {len(versions)} versions au total)",
         Counter(v["acteur"] for v in versions if le_jour(v["le"])))
afficher(f"Journal Scan — événements le {jour:%d/%m/%Y}",
         Counter((a["event"], a.get("actor")) for a in audit if le_jour(a["at_time"])))

col = sources.collecter(cfg, tz)
P, E, V = I.preparer(col.pieces, col.evenements, col.personnes, cfg, col.agent)
v = V[V["le"].dt.date == jour]
print(f"\n=== Retenus comme indexeurs le {jour:%d/%m/%Y}")
for a, g in v.groupby("acteur"):
    print(f"  {len(g):6}  {col.personnes.get(a, a)} [{a}]  datation : {dict(g['source'].value_counts())}")
print(f"\nComptes écartés (perimetre.comptes_techniques) : {cfg['perimetre'].get('comptes_techniques')}")
