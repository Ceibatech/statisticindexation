"""Classeur Excel joint au rapport : synthèse, indexeurs, jours, pièces (chronologie complète), événements."""
from __future__ import annotations

import io
import json

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .indicateurs import LIBELLES_CHAMPS, LIBELLES_ETATS, Rapport

ENTETE = PatternFill("solid", fgColor="13293D")
BLANC = Font(color="FFFFFF", bold=True)
GRAS = Font(bold=True)


def _feuille(wb, titre, colonnes, lignes, largeurs=None, formats=None):
    ws = wb.create_sheet(titre)
    ws.append(colonnes)
    for c in ws[1]:
        c.fill, c.font = ENTETE, BLANC
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for l in lignes:
        ws.append([None if (isinstance(v, float) and pd.isna(v)) or v is pd.NaT else v for v in l])
    ws.freeze_panes = "A2"
    if lignes:
        ws.auto_filter.ref = ws.dimensions
    for i, col in enumerate(colonnes, 1):
        ws.column_dimensions[get_column_letter(i)].width = (largeurs or {}).get(col, max(12, min(40, len(str(col)) + 4)))
        fmt = (formats or {}).get(col)
        if fmt:
            for cell in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                cell[0].number_format = fmt
    return ws


def _py(v):
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.to_pydatetime()
    if hasattr(v, "item"):
        return v.item()
    return v


def classeur(r: Rapport, cfg: dict, ctx: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Synthèse"
    ws["A1"], ws["A1"].font = ctx["titre"], Font(bold=True, size=14)
    ws["A2"] = ctx["sous_titre"]
    ws["A3"] = f"Source : {ctx['source']} · conçu par {cfg['rapport'].get('auteur', '')}, {cfg['rapport'].get('societe', '')}"
    ligne = 5
    for bloc in (ctx["tuiles"], ctx["tuiles_q"]):
        for t in bloc:
            ws.cell(ligne, 1, t["libelle"]).font = GRAS
            ws.cell(ligne, 2, t["valeur"])
            ws.cell(ligne, 3, t.get("comp") or t.get("detail") or t.get("taux_txt") or "")
            ligne += 1
    ligne += 1
    ws.cell(ligne, 1, "Lecture de l'analyste").font = Font(bold=True, size=12)
    for p in ctx["points"]:
        ligne += 1
        ws.cell(ligne, 1, p["etiquette"]).font = GRAS
        ws.cell(ligne, 2, p["titre"])
        ws.cell(ligne, 3, p["texte"])
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width, ws.column_dimensions["C"].width = 30, 34, 110

    I = r.indexeurs
    if not I.empty:
        _feuille(wb, "Par indexeur",
                 ["Indexeur", "Identifiant", "Validations", "Pièces indexées (1re validation)", "Part", "Temps médian (s)",
                  "Temps 90 % (s)", "Temps mesurés", "Heures d'indexation", "Pièces / heure", "Champs corrigés / pièce",
                  "Acceptées sans correction", "Versées", "Saisies non validées", "Première validation", "Dernière validation"],
                 [[x["nom"], a, _py(x["validations"]), _py(x["indexees"]), _py(x["part"]), _py(x["temps_med"]), _py(x["temps_p90"]),
                   _py(x["mesures"]), _py(x["heures"]), _py(x["cadence"]), _py(x["corrections"]), _py(x["sans_correction"]),
                   _py(x["versees"]), _py(x["en_cours"]), _py(x["premiere"]), _py(x["derniere"])] for a, x in I.iterrows()],
                 {"Indexeur": 24, "Identifiant": 22},
                 {"Part": "0%", "Acceptées sans correction": "0%", "Temps médian (s)": "0", "Temps 90 % (s)": "0",
                  "Heures d'indexation": "0.0", "Pièces / heure": "0.0", "Champs corrigés / pièce": "0.00",
                  "Première validation": "dd/mm/yyyy hh:mm", "Dernière validation": "dd/mm/yyyy hh:mm"})

    F = r.flux
    _feuille(wb, "Par jour", ["Jour", "Déposées", "Analysées", "Indexées", "Versées", "Indexeurs actifs",
                              "Temps médian (s)", "Stock à indexer le soir"],
             [[x["jour"], x["deposees"], x["analysees"], x["indexees"], x["versees"], x["indexeurs"], _py(x["temps_med"]),
               x["stock_soir"]] for _, x in F.iterrows()], formats={"Jour": "dd/mm/yyyy", "Temps médian (s)": "0"})

    M = r.matrice
    if not M.empty:
        _feuille(wb, "Jour × indexeur", ["Jour", *M.columns, "Total"],
                 [[j, *[int(v) for v in row], int(row.sum())] for j, row in M.iterrows()], formats={"Jour": "dd/mm/yyyy"})

    P = r.pieces
    V = r.validations
    temps = V.drop_duplicates("piece").set_index("piece")["temps_s"]
    cols = ["Pièce (nodeId)", "Nom", "Zone", "Conteneur", "Carton", "Dossier", "État", "Déposée le", "Déposée par",
            "Analysée le", "Nature reconnue", "Anomalies", "Indexée le", "Indexée par", "Temps d'indexation (s)",
            "Attente avant indexation (h)", "Validations", "Champs corrigés", "Type documentaire", "Rubrique", "Versée le",
            "Versée par", "Relances", "Échecs d'analyse", "Dernière modification", "Modifiée par"]
    lignes = []
    for _, x in P.sort_values("depose_le", na_position="last").iterrows():
        att = (x["valide_le"] - x["entree_stock"]).total_seconds() / 3600 if pd.notna(x["valide_le"]) and pd.notna(x["entree_stock"]) else None
        lignes.append([x["id"], x["nom"], x["zone"], x["conteneur"], x["carton"], x["dossier"],
                       LIBELLES_ETATS.get(x["etat"], x["etat"]), _py(x["depose_le"]), x["depose_par"], _py(x["analyse_le"]),
                       x["nature_reconnue"], ", ".join(json.loads(x["anomalies"] or "[]")), _py(x["valide_le"]),
                       r_nom(x["valide_par"], r), _py(temps.get(x["id"])), att, int(x["validations"]),
                       ", ".join(LIBELLES_CHAMPS.get(c, c) for c in x["champs_corriges"]), x["type_document"], x["rubrique"],
                       _py(x["verse_le"]), r_nom(x["verse_par"], r), int(x["relances"]), int(x["echecs_analyse"]),
                       _py(x["modifie_le"]), r_nom(x["modifie_par"], r)])
    dt = "dd/mm/yyyy hh:mm"
    _feuille(wb, "Pièces", cols, lignes, {"Pièce (nodeId)": 38, "Nom": 36, "Champs corrigés": 30},
             {"Déposée le": dt, "Analysée le": dt, "Indexée le": dt, "Versée le": dt, "Dernière modification": dt,
              "Temps d'indexation (s)": "0", "Attente avant indexation (h)": "0.0"})

    _feuille(wb, "Méthode", ["Indicateur", "Définition"], [
        ["Pièce indexée", "Pièce dont les métadonnées ont été validées (METADATA_APPROVED, journal du service Scan) ; comptée à sa 1re validation."],
        ["Indexeur", "Utilisateur Alfresco auteur de la validation ; les comptes techniques sont exclus."],
        ["Temps d'indexation", f"Écart entre deux validations successives du même indexeur le même jour, entre {cfg['mesure']['temps_min_s']} s et "
                               f"{cfg['mesure']['session_max_min']} min. La 1re pièce d'une session n'est pas mesurée."],
        ["Heures d'indexation", "Somme des temps mesurés + temps médian pour chaque pièce non mesurée."],
        ["Attente avant indexation", "De la fin de l'analyse automatique (à défaut, du dépôt) à la validation."],
        ["Champs corrigés", "Champs mulcv:* dont la valeur validée diffère de la proposition de l'analyse automatique."],
        ["Stock à indexer", "Pièces analysées non encore validées (hors analyses en échec)."],
        ["Versement", "Record déclaré et classé dans le plan RM (RECORD_FILED)."],
        ["Écart significatif", "a + b ≥ 20 et |a − b| / √(a + b) ≥ 2."],
    ], {"Indicateur": 26, "Définition": 120})
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def r_nom(u, r: Rapport):
    if u is None or (isinstance(u, float) and pd.isna(u)):
        return None
    return getattr(r, "personnes", {}).get(u, u)
