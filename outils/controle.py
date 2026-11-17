#!/usr/bin/env python3
"""Contrôle manuel des chiffres, sans rapport ni envoi.

    sudo .venv/bin/python outils/controle.py                 # aujourd'hui
    sudo .venv/bin/python outils/controle.py 2026-11-17      # un jour donné
    sudo .venv/bin/python outils/controle.py 2026-11-10 7    # 7 jours à partir du 10/11
    .venv/bin/python outils/controle.py --demo

Affiche, par jour et par indexeur : validations, temps médian, versements ; puis l'état courant des pièces.
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
from indexation import indicateurs as I, sources  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
cfg = yaml.safe_load(open(RACINE / "config.yaml", encoding="utf-8"))
if "--demo" in sys.argv:
    cfg["source"]["type"] = "demo"
tz = ZoneInfo(cfg["projet"]["fuseau_horaire"])
debut = date.fromisoformat(args[0]) if args else datetime.now(tz).date()
n = int(args[1]) if len(args) > 1 else 1
col = sources.collecter(cfg, tz, datetime.combine(debut + timedelta(days=n), datetime.min.time()) if cfg["source"]["type"] == "demo" else None)
print(f"Source : {col.mode} — {len(col.pieces)} pièces, {len(col.evenements)} événements")
P, E, V = I.preparer(col.pieces, col.evenements, col.personnes, cfg)
for k in range(n):
    j = debut + timedelta(days=k)
    v = V[V["le"].dt.date == j]
    print(f"\n{I.JOURS[j.weekday()]} {j:%d/%m/%Y} : {len(v)} validations, {int((P['valide_le'].dt.date == j).sum())} pièces indexées, "
          f"{int((P['verse_le'].dt.date == j).sum())} versées")
    for a, g in v.groupby("acteur"):
        print(f"  {col.personnes.get(a, a):28} {len(g):5}  médiane {g['temps_s'].median() or 0:6.0f} s  "
              f"{g['le'].min():%H:%M}–{g['le'].max():%H:%M}")
print("\nÉtat courant :")
for e, c in P["etat"].fillna("NON_SUIVI").value_counts().items():
    print(f"  {I.LIBELLES_ETATS.get(e, e):40} {c:6}")
