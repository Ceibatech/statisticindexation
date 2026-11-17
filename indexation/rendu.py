"""Mise en forme : HTML du mail (tableau de bord), texte brut, images."""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import graphiques
from .indicateurs import JOURS, LIBELLES_ANOMALIES, LIBELLES_CHAMPS, LIBELLES_ETATS, Rapport, significatif

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
NIVEAUX = {
    "critique": ("#c62828", "#7f1d1d", "#fde7e7", "■", "Critique"),
    "attention": ("#d98a00", "#6b4500", "#fff1d6", "▲", "Attention"),
    "ok": ("#1f8a4c", "#14532d", "#e2f4ea", "●", "Conforme"),
    "info": ("#2a78d6", "#1e3a5f", "#e5effb", "ℹ", "Info"),
}


def _nan(x):
    return x is None or (isinstance(x, float) and math.isnan(x)) or (not isinstance(x, (str, list, dict)) and pd.isna(x))


def n(x) -> str:
    return "—" if _nan(x) else f"{int(round(float(x))):,}".replace(",", " ")


def pct(x) -> str:
    return "—" if _nan(x) else f"{x * 100:.0f} %"


def duree(s) -> str:
    """Secondes -> « 1 min 42 s »."""
    if _nan(s):
        return "—"
    s = float(s)
    if s < 90:
        return f"{s:.0f} s"
    if s < 3600:
        return f"{int(s // 60)} min {int(s % 60):02d} s"
    return f"{int(s // 3600)} h {int(s % 3600 // 60):02d}"


def heures(h) -> str:
    if _nan(h):
        return "—"
    if h < 1:
        return f"{h * 60:.0f} min"
    if h < 48:
        return f"{int(h)} h {int((h % 1) * 60):02d}"
    return f"{h / 24:.1f} j".replace(".", ",")


def date_longue(d) -> str:
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]} {d.year}"


def ecart(a, b, lib) -> str | None:
    if _nan(b) or b is None:
        return None
    if not significatif(a, b):
        return f"≈ {lib} ({n(b)})"
    return f"{'+' if a >= b else '−'}{abs(a - b) / b * 100:.0f} % vs {lib} ({n(b)})" if b else None


def contexte(r: Rapport, cfg: dict, cal) -> tuple[dict, dict[str, bytes], str]:
    c, ref, st, obj, hebdo = r.cur, r.ref, r.stock, cfg["objectifs"], r.type == "hebdo"
    lib_ref = "sem. préc." if hebdo else "moy. 10 j"
    objectif = obj["pieces_indexees_par_jour"] * (len(cal_jours_semaine(r, cal)) if hebdo else 1)
    taux = c["indexees"] / objectif if objectif else None
    tuiles = [
        {"libelle": "Pièces indexées", "valeur": n(c["indexees"]), "taux": taux,
         "barre": max(2, min(100, round((taux or 0) * 100))), "taux_txt": f"{pct(taux)} de l'objectif ({n(objectif)})",
         "comp": ecart(c["indexees"], ref.get("indexees"), lib_ref)},
        {"libelle": "Temps médian par pièce", "valeur": duree(c["temps_med"]), "taux": None,
         "comp": f"90 % en moins de {duree(c['temps_p90'])} · cible {duree(obj['temps_cible_s'])}"},
        {"libelle": "Indexeurs actifs", "valeur": n(c["indexeurs"]), "taux": None,
         "comp": f"{heures(c['heures'])} d'indexation · {n(c['cadence'])} pièces/h"},
        {"libelle": "Stock à indexer", "valeur": n(st["a_indexer"]), "taux": None,
         "comp": (f"plus ancienne : {st['plus_ancien']:%d/%m %H:%M}" if st["plus_ancien"] is not None else "file vide")},
    ]
    tuiles_q = [
        {"libelle": "Validées par l'archiviste", "valeur": n(c["validees"]),
         "detail": f"{n(c['versees'])} versées aux archives"
                   + (f" · validation {duree(c['delai_validation_min'] * 60)} après" if c["delai_validation_min"] is not None else "")},
        {"libelle": "Attente avant indexation", "valeur": heures(c["delai_attente_h"]),
         "detail": f"médiane · 90 % en moins de {heures(c['delai_attente_p90_h'])}"},
        {"libelle": "Acceptées sans correction", "valeur": pct(c["sans_correction"]),
         "detail": f"{c['corrections_moy']:.1f} champ(s) corrigé(s) par pièce".replace(".", ",") if c["corrections_moy"] is not None else "—"},
        {"libelle": "Reconnaissance automatique", "valeur": pct(c["reconnaissance"]),
         "detail": f"{pct(c['avec_anomalie'])} des analyses avec anomalie"},
    ]
    points = []
    for p in r.constats:
        col, txt, fond, sym, lab = NIVEAUX[p["niveau"]]
        points.append({**p, "couleur": col, "couleur_txt": txt, "fond": fond, "symbole": sym, "etiquette": lab})

    # Indexeurs
    I = r.indexeurs
    entetes = [{"l": "Indexeur"}, {"l": "Indexées", "d": 1}, {"l": "Part", "w": 70}, {"l": "Temps médian", "d": 1},
               {"l": "90 % <", "d": 1}, {"l": "Pièces/h", "d": 1}, {"l": "Validées", "d": 1},
               {"l": "Horaires", "d": 1}]
    lignes = []
    if not I.empty:
        mx = I["validations"].max()
        for acteur, x in I.head(cfg["rapport"]["indexeurs_max"]).iterrows():
            sous = []
            if not _nan(x.get("ref")) and "ref" in x:
                sous.append(f"moy. {n(x['ref'])}/j")
            if x.get("en_cours"):
                sous.append(f"{n(x['en_cours'])} non validée(s)")
            lignes.append([{"v": x["nom"], "g": 1, "s": acteur if acteur != x["nom"] else None},
                           {"v": n(x["validations"]), "g": 1, "s": " · ".join(sous) or None},
                           {"barre": max(2, round(x["validations"] / mx * 70))},
                           {"v": duree(x["temps_med"]), "s": f"{n(x['mesures'])} mesurés"},
                           {"v": duree(x["temps_p90"])}, {"v": n(x["cadence"])},
                           {"v": n(x["validees"])},
                           {"v": f"{x['premiere']:%H:%M}–{x['derniere']:%H:%M}" if not hebdo else f"{x['premiere']:%d/%m}–{x['derniere']:%d/%m}"}])
    t_ind = {"titre": "Par indexeur", "entetes": entetes, "lignes": lignes,
             "total": ["Équipe", n(c["validations"]), "", duree(c["temps_med"]), duree(c["temps_p90"]), n(c["cadence"]),
                       n(c["validees"]), ""],
             "note": "Indexée = métadonnées enregistrées par l'indexeur. Temps : écart entre deux pièces indexées "
                     f"successivement par la même personne, entre {cfg['mesure']['temps_min_s']} s et "
                     f"{cfg['mesure']['session_max_min']} min. Pièces/h : pièces indexées rapportées aux heures d'indexation. "
                     "Validées : contrôle de l'archiviste dans le panneau Scan."}
    if not lignes:
        t_ind["note"] = "Aucune validation sur la période."

    # Parcours des pièces (états courants)
    ordre = ["NON_SUIVI", "QUEUED", "RETRY", "ANALYSING", "ERROR", "REVIEW_REQUIRED", "APPROVED", "DECLARING", "FILING",
             "FILE_ERROR", "ARCHIVED"]
    tot = int(r.etats.sum()) or 1
    t_etats = {"titre": "Où en sont les pièces", "entetes": [{"l": "État"}, {"l": "Pièces", "d": 1}, {"l": "Part", "d": 1}],
               "lignes": [[{"v": LIBELLES_ETATS.get(e, e)}, {"v": n(r.etats.get(e, 0))}, {"v": pct(r.etats.get(e, 0) / tot)}]
                          for e in ordre if r.etats.get(e, 0)],
               "total": ["Total suivi", n(tot), "100 %"]}
    age = {k: v for k, v in st["age"].items() if not k.startswith("_")}
    t_age = {"titre": "Ancienneté du stock à indexer (jours ouvrés)",
             "entetes": [{"l": k, "d": 1} for k in age], "lignes": [[{"v": n(v), "g": 1} for v in age.values()]],
             "note": (f"Rythme des 10 derniers jours : {n(st['rythme'])} indexées / {n(st['entree'])} nouvelles par jour."
                      + (f" Résorption estimée le {st['date_resorption']:%d/%m/%Y}." if st["date_resorption"] else ""))}

    # Qualité
    ch = c["champs"].head(6)
    t_champs = {"titre": "Champs corrigés par les indexeurs", "entetes": [{"l": "Champ"}, {"l": "Pièces", "d": 1}, {"l": "Part des pièces validées", "d": 1}],
                "lignes": [[{"v": LIBELLES_CHAMPS.get(k, k.replace("mulcv:", ""))}, {"v": n(v)}, {"v": pct(v / c["validees"]) if c["validees"] else "—"}]
                           for k, v in ch.items()],
                "note": "Pièces validées : proposition de l'analyse automatique comparée à la valeur validée."}
    an = c["anomalies"].head(5)
    t_anom = {"titre": "Anomalies relevées par l'analyse", "entetes": [{"l": "Anomalie"}, {"l": "Pièces", "d": 1}],
              "lignes": [[{"v": LIBELLES_ANOMALIES.get(k, k)}, {"v": n(v)}] for k, v in an.items()],
              "note": f"{n(c['analysees'])} pièces analysées · {n(c['echecs_analyse'])} échec(s) · {n(c['relances'])} relance(s) demandée(s)."}
    rb = r.rubriques.head(cfg["rapport"]["rubriques_max"])
    t_rub = {"titre": "Rubriques de classement des pièces indexées",
             "entetes": [{"l": "Code"}, {"l": "Rubrique"}, {"l": "Pièces", "d": 1}],
             "lignes": [[{"v": k, "g": 1}, {"v": x["chemin"] or "—"}, {"v": n(x["pieces"])}] for k, x in rb.iterrows()]}

    cu = r.cumul
    vue = {"titre": "Depuis le début de l'indexation", "sous_titre": f"depuis le {datetime.fromisoformat(cfg['projet']['date_debut']):%d/%m/%Y}",
           "tuiles": [
               {"libelle": "Pièces indexées", "valeur": n(cu["indexees"]), "detail": f"sur {n(cu['pieces'])} pièces suivies",
                "barre": round(cu["indexees"] / cu["pieces"] * 100) if cu["pieces"] else 0},
               {"libelle": "Pièces validées", "valeur": n(cu["validees"]), "detail": f"dont {n(cu['versees'])} versées aux archives",
                "barre": round(cu["validees"] / cu["pieces"] * 100) if cu["pieces"] else 0},
               {"libelle": "Dossiers entièrement indexés", "valeur": n(cu["dossiers_acheves"]), "detail": f"sur {n(cu['dossiers'])} dossiers",
                "barre": round(cu["dossiers_acheves"] / cu["dossiers"] * 100) if cu["dossiers"] else 0},
               {"libelle": "Temps médian global", "valeur": duree(cu["temps_med"]), "detail": f"{n(cu['indexeurs'])} indexeurs au total"}]}

    # Graphiques
    images = {}
    F = r.flux
    if hebdo:
        jours_sem = [d for d in F["jour"] if d >= r.debut.date()]
        Fs = F[F["jour"].isin(jours_sem)]
        images["g_activite"] = graphiques.quotidien(list(Fs["jour"]), list(Fs["indexees"]), obj["pieces_indexees_par_jour"])
        g1 = ("Pièces indexées par jour de la semaine", "Objectif quotidien en pointillé")
    else:
        h = r.horaire
        images["g_activite"] = graphiques.horaire(h["heures"], h["aujourdhui"], h["reference"], h["coupure"],
                                                  "Moyenne des 10 derniers jours")
        g1 = ("Indexations heure par heure", "Pièces indexées dans la journée et moyenne des 10 derniers jours d'indexation")
    V = r.validations
    tv = V[(V["le"] >= r.debut) & (V["le"] < r.fin)]["temps_s"].dropna()
    if len(tv) >= 5:
        images["g_temps"] = graphiques.distribution_temps(list(tv), c["temps_med"], obj["temps_cible_s"])
    images["g_tendance"] = graphiques.quotidien(list(F["jour"]), list(F["indexees"]), obj["pieces_indexees_par_jour"])
    images["g_stock"] = graphiques.stock(list(F["jour"]), list(F["stock_soir"]))

    zone = "Zone Sud"
    sous_titre = (f"{date_longue(r.maintenant.date())} · situation à {r.maintenant:%H:%M}" if not hebdo
                  else f"{r.libelle} · situation au {date_longue(r.maintenant.date())} {r.maintenant:%H:%M}")
    titre = "Indexation SAE — " + ("récapitulatif hebdomadaire" if hebdo else "point du jour")
    ctx = {
        "titre": titre, "sous_titre": sous_titre, "projet": cfg["projet"]["nom"], "client": cfg["projet"]["client"],
        "auteur": cfg["rapport"].get("auteur"), "societe": cfg["rapport"].get("societe"),
        "preheader": f"{n(c['indexees'])} pièces indexées · temps médian {duree(c['temps_med'])} · {n(st['a_indexer'])} à indexer",
        "libelle_production": r.libelle + (" (jusqu'à " + f"{r.maintenant:%H:%M})" if not hebdo else ""),
        "tuiles": tuiles, "tuiles_q": tuiles_q, "points": points,
        "g1_titre": g1[0], "g1_sous_titre": g1[1], "g_temps": "g_temps" in images,
        "t_ind": t_ind, "t_etats": t_etats, "t_age": t_age, "t_champs": t_champs, "t_anom": t_anom, "t_rub": t_rub,
        "vue": vue, "source": r.source, "demo": r.mode == "demo", "degrade": r.mode == "degrade",
        "genere": f"{r.maintenant:%d/%m/%Y à %H:%M}", "prochain": prochain_envoi(r.maintenant, cal),
        "rectificatif": None, "pieces_jointes": None, "zone": zone,
        "session": cfg["mesure"]["session_max_min"],
        "datation": datation(c),
    }
    sujet = (f"[Indexation SAE] {('Semaine du ' + f'{r.debut:%d/%m}') if hebdo else f'{r.maintenant:%d/%m}'} — "
             f"{n(c['indexees'])} pièces indexées, {duree(c['temps_med'])}/pièce, {n(st['a_indexer'])} en attente")
    if r.mode == "demo":
        sujet = "[DÉMO] " + sujet
    return ctx, images, sujet


def datation(c) -> str:
    s = c.get("sources") or {}
    tot = sum(s.values())
    if not tot:
        return ""
    lib = {"version": "historique des versions Alfresco", "derniere_modification": "dernière modification",
           "validation": "validation (enregistrement non daté)"}
    return "Datation des indexations de la période : " + ", ".join(
        f"{v * 100 / tot:.0f} % {lib.get(k, k)}" for k, v in sorted(s.items(), key=lambda x: -x[1])) + "."


def cal_jours_semaine(r: Rapport, cal) -> list:
    lundi = r.debut.date()
    return [lundi + timedelta(days=i) for i in range(7) if cal.ouvre(lundi + timedelta(days=i))
            and lundi + timedelta(days=i) <= r.maintenant.date()]


def prochain_envoi(maintenant: datetime, cal) -> str:
    j = maintenant.date()
    for _ in range(10):
        if cal.ouvre(j) and datetime.combine(j, time(18, 5)) > maintenant + timedelta(minutes=5):
            suff = " + récapitulatif hebdomadaire (18h20)" if j.weekday() == 4 else ""
            return f"{date_longue(j)}, 18h05{suff}"
        j += timedelta(days=1)
    return "—"


def rendre(r: Rapport, cfg: dict, cal, rectificatif: str | None = None, pieces_jointes: str | None = None):
    ctx, images, sujet = contexte(r, cfg, cal)
    ctx["rectificatif"], ctx["pieces_jointes"] = rectificatif, pieces_jointes
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape(["html", "j2"]))
    html = env.get_template("rapport.html.j2").render(**ctx)
    texte = "\n".join([ctx["titre"], ctx["sous_titre"], ""]
                      + [f"- {t['libelle']} : {t['valeur']}" + (f" ({t['comp']})" if t.get("comp") else "") for t in ctx["tuiles"]]
                      + [f"- {t['libelle']} : {t['valeur']} ({t['detail']})" for t in ctx["tuiles_q"]]
                      + ["", "Lecture de l'analyste :"] + [f"[{p['etiquette']}] {p['titre']} — {p['texte']}" for p in ctx["points"]]
                      + ["", "Par indexeur :"]
                      + [f"- {l[0]['v']} : {l[1]['v']} pièces, temps médian {l[3]['v']}, {l[5]['v']} pièces/h" for l in ctx["t_ind"]["lignes"]]
                      + ["", f"Source : {ctx['source']} · généré le {ctx['genere']}"])
    return sujet, html, texte, images, ctx
