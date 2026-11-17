"""Indicateurs de l'indexation (voir docs/INDICATEURS.md pour les définitions).

Grains :
  pièce indexée     = pièce dont les métadonnées ont été validées (événement METADATA_APPROVED) ;
                      comptée une fois, à sa PREMIÈRE validation. Une validation suivante est une reprise.
  indexeur          = acteur de la validation (identifiant Alfresco ; nom lu dans le profil Alfresco)
  temps d'indexation = écart entre deux validations consécutives d'un même indexeur, s'il est compris entre
                      mesure.temps_min_s et mesure.session_max_min (travail enchaîné). La première pièce d'une
                      session n'a pas de temps mesuré ; elle compte pour le temps médian de l'indexeur dans
                      les heures actives.
  versement         = événement RECORD_FILED (record déclaré et classé dans le plan RM)
  stock à indexer   = pièce analysée, pas encore validée
Une variation n'est commentée que si elle est significative : a + b ≥ 20 et |z| ≥ 2, z = (a − b) / √(a + b).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
# propriétés posées par le serveur ou dérivées : jamais comptées comme corrections de l'indexeur
TECHNIQUES = {"mulcv:sha256", "mulcv:qualityStatus", "mulcv:classificationStatus", "mulcv:ocrStatus", "mulcv:sourceSystem",
              "mulcv:sourceId", "mulcv:classPath", "mulcv:classCode", "mulcv:planVersion", "$type"}
LIBELLES_CHAMPS = {
    "mulcv:nature": "Nature", "mulcv:typeDocumentCode": "Type documentaire", "mulcv:classId": "Rubrique de classement",
    "mulcv:subject": "Objet", "mulcv:dossierId": "Identifiant du dossier", "mulcv:boxReference": "Carton",
    "mulcv:lotNumber": "N° lot", "mulcv:blockNumber": "N° îlot", "mulcv:surfaceArea": "Superficie",
    "mulcv:municipality": "Commune", "mulcv:landTitleNumber": "N° titre foncier", "mulcv:subdivision": "Lotissement",
    "mulcv:dateOrYear": "Date / année", "mulcv:recipient": "Destinataire", "mulcv:provenanceLabel": "Provenance",
    "mulcv:reference": "Référence", "mulcv:archivalSite": "Site d'archivage", "mulcv:year": "Année",
}
LIBELLES_ANOMALIES = {
    "SURFACE_CONFLICT": "Superficies contradictoires", "DESTINATION_REQUIRES_REVIEW": "Destination à choisir",
    "MUNICIPALITY_DIFFERS_FROM_COTE": "Commune ≠ cote du dossier", "DOSSIER_ID_CONFLICT": "Identifiant de dossier en conflit",
}
LIBELLES_ETATS = {
    "NON_SUIVI": "Déposée, pas encore prise en charge", "QUEUED": "En file d'analyse", "RETRY": "Analyse relancée",
    "ANALYSING": "Analyse en cours", "ERROR": "Analyse en échec", "REVIEW_REQUIRED": "À indexer (vérification)",
    "APPROVED": "Indexée, à verser", "DECLARING": "Versement en cours", "FILING": "Versement en cours",
    "FILE_ERROR": "Versement en échec", "ARCHIVED": "Versée aux archives",
}


@dataclass
class Calendrier:
    jours_ouvres: list[int]
    feries: set[date]
    debut: time
    fin: time

    def ouvre(self, j: date) -> bool:
        return j.weekday() in self.jours_ouvres and j not in self.feries

    def precedents(self, j: date, n: int) -> list[date]:
        out, d = [], j - timedelta(days=1)
        while len(out) < n and d > j - timedelta(days=200):
            if self.ouvre(d):
                out.append(d)
            d -= timedelta(days=1)
        return out

    def ouvres_entre(self, a: date, b: date) -> int:
        """Jours ouvrés dans ]a, b]."""
        n, d = 0, a
        while d < b:
            d += timedelta(days=1)
            n += self.ouvre(d)
        return n

    def apres(self, j: date, n: int) -> date:
        d, k = j, 0
        while k < n:
            d += timedelta(days=1)
            k += self.ouvre(d)
        return d


def significatif(a: float, b: float) -> bool:
    return a + b >= 20 and abs(a - b) / math.sqrt(a + b) >= 2


def _j(x) -> dict:
    try:
        v = json.loads(x) if isinstance(x, str) and x else {}
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _vide(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, (list, dict)) and not v)


def corrections(proposition: str, validation: str) -> list[str]:
    """Champs dont la valeur validée diffère de la proposition de l'analyse automatique."""
    p, v = _j(proposition), _j(validation)
    if not v:
        return []
    out = []
    for k in sorted(set(p) | set(v)):
        if k in TECHNIQUES or not k.startswith("mulcv:"):
            continue
        a, b = p.get(k), v.get(k)
        if _vide(a) and _vide(b):
            continue
        if str(a if a is not None else "").strip() != str(b if b is not None else "").strip():
            out.append(k)
    return out


# ---------------------------------------------------------------------------
#  Préparation : une ligne par pièce, une ligne par événement
# ---------------------------------------------------------------------------
def preparer(pieces: list[dict], evenements: list[dict], personnes: dict, cfg: dict):
    P = pd.DataFrame(pieces)
    E = pd.DataFrame(evenements, columns=["id", "piece", "evenement", "acteur", "le", "details"])
    for c in ("depose_le", "modifie_le"):
        P[c] = pd.to_datetime(P[c], errors="coerce")
    E["le"] = pd.to_datetime(E["le"], errors="coerce")
    exclus = cfg["perimetre"].get("exclure_conteneurs")
    if exclus:
        hors = P["conteneur"].fillna("").str.contains(exclus, regex=True)
        E = E[~E["piece"].isin(set(P.loc[hors, "id"]))]
        P = P[~hors]
    tech = re.compile(cfg["perimetre"].get("comptes_techniques") or r"^$")
    E["humain"] = E["acteur"].fillna("").map(lambda a: bool(a) and not tech.search(a))
    P["saisie_humaine"] = P["modifie_par"].fillna("").map(lambda a: bool(a) and not tech.search(a))

    def premier(evt, col="le", agg="min", humain=True):
        x = E[(E["evenement"] == evt) & (E["humain"] if humain else True)].sort_values(["le", "id"])
        if agg == "min":
            return x.groupby("piece")[col].first()
        if agg == "count":
            return x.groupby("piece").size()
        return x.groupby("piece")[col].last()

    P = P.set_index("id")
    # analyse : comptes techniques ; validation, versement et relance : personnes uniquement
    P["analyse_le"] = premier("ANALYSIS_COMPLETED", humain=False)
    P["echecs_analyse"] = premier("ANALYSIS_FAILED", agg="count", humain=False)
    P["relances"] = premier("ANALYSIS_REQUESTED", agg="count")
    P["valide_le"] = premier("METADATA_APPROVED")
    P["valide_par"] = premier("METADATA_APPROVED", col="acteur")
    P["validations"] = premier("METADATA_APPROVED", agg="count")
    P["derniere_validation"] = premier("METADATA_APPROVED", agg="last")
    P["verse_le"] = premier("RECORD_FILED")
    P["verse_par"] = premier("RECORD_FILED", col="acteur")
    for c in ("echecs_analyse", "relances", "validations"):
        P[c] = P[c].fillna(0).astype(int)
    # Une pièce dont l'analyse n'a laissé aucune trace d'événement (anciennes versions) : date de dépôt
    P["entree_stock"] = P["analyse_le"].fillna(P["depose_le"])
    P["champs_corriges"] = [corrections(a, b) for a, b in zip(P["proposition"].fillna(""), P["validation"].fillna(""))]
    P["nb_corrections"] = P["champs_corriges"].map(len)
    P["corrigee"] = P["nb_corrections"] > 0
    P["reconnue"] = P["nature_reconnue"].map(lambda x: None if x in (None, "") or pd.isna(x) else x != "inconnu")
    P["nb_anomalies"] = P["anomalies"].fillna("[]").map(lambda x: len(json.loads(x)) if x else 0)
    P = P.reset_index()

    # Temps d'indexation : écart avec la validation précédente du même indexeur, le même jour
    V = E[(E["evenement"] == "METADATA_APPROVED") & E["humain"]].sort_values(["acteur", "le", "id"]).copy()
    V["jour"] = V["le"].dt.date
    V["ecart_s"] = V.groupby(["acteur", "jour"])["le"].diff().dt.total_seconds()
    m = cfg["mesure"]
    V["mesure"] = V["ecart_s"].between(m["temps_min_s"], m["session_max_min"] * 60)
    V["temps_s"] = V["ecart_s"].where(V["mesure"])
    V["en_lot"] = V["ecart_s"] < m["temps_min_s"]
    V["premiere"] = V.duplicated("piece", keep="first").map(lambda d: not d)
    V["nom"] = V["acteur"].map(lambda a: personnes.get(a) or a)
    return P, E, V


# ---------------------------------------------------------------------------
#  Calculs sur une période [a, b[
# ---------------------------------------------------------------------------
def _entre(s: pd.Series, a, b):
    return (s >= a) & (s < b)


def _q(s: pd.Series, q):
    s = s.dropna()
    return float(s.quantile(q)) if len(s) else None


def heures_actives(v: pd.DataFrame) -> float:
    """Heures d'indexation : temps mesurés + temps médian de l'indexeur pour chaque pièce non mesurée."""
    if v.empty:
        return 0.0
    med = v["temps_s"].median()
    med = 0 if pd.isna(med) else med
    non = (~v["mesure"] & ~v["en_lot"]).sum()
    return float((v["temps_s"].sum(skipna=True) + non * med) / 3600)


def periode(P, E, V, a: datetime, b: datetime) -> dict:
    ind = P[_entre(P["valide_le"], a, b)]
    v = V[_entre(V["le"], a, b)]
    vers = P[_entre(P["verse_le"], a, b)]
    anal = P[_entre(P["analyse_le"], a, b)]
    h = heures_actives(v)
    delai_dep = (ind["valide_le"] - ind["depose_le"]).dt.total_seconds() / 3600
    delai_att = (ind["valide_le"] - ind["entree_stock"]).dt.total_seconds() / 3600
    delai_ver = (vers["verse_le"] - vers["valide_le"]).dt.total_seconds() / 60
    champs = pd.Series([c for l in ind["champs_corriges"] for c in l], dtype=object).value_counts()
    anomalies = pd.Series([w for x in anal["anomalies"].fillna("[]") for w in json.loads(x)], dtype=object)
    anomalies = anomalies.map(lambda w: "Valeurs en conflit" if str(w).startswith("CONFLICT_") else w).value_counts()
    return {
        "indexees": int(len(ind)), "reprises": int((~v["premiere"]).sum()), "validations": int(len(v)),
        "indexeurs": int(v["acteur"].nunique()), "versees": int(len(vers)),
        "deposees": int(_entre(P["depose_le"], a, b).sum()), "analysees": int(len(anal)),
        "echecs_analyse": int(((E["evenement"] == "ANALYSIS_FAILED") & _entre(E["le"], a, b)).sum()),
        "relances": int(((E["evenement"] == "ANALYSIS_REQUESTED") & E["humain"] & _entre(E["le"], a, b)).sum()),
        "temps_med": _q(v["temps_s"], .5), "temps_p90": _q(v["temps_s"], .9), "temps_moy": (float(v["temps_s"].mean()) if v["temps_s"].notna().any() else None),
        "temps_mesures": int(v["mesure"].sum()), "en_lot": int(v["en_lot"].sum()),
        "heures": h, "cadence": (len(v) / h) if h > 0 else None,
        "delai_depot_h": _q(delai_dep, .5), "delai_attente_h": _q(delai_att, .5), "delai_attente_p90_h": _q(delai_att, .9),
        "delai_versement_min": _q(delai_ver, .5),
        "sans_correction": (float((~ind["corrigee"]).mean()) if len(ind) else None),
        "corrections_moy": (float(ind["nb_corrections"].mean()) if len(ind) else None),
        "champs": champs, "reconnaissance": (float(anal["reconnue"].dropna().mean()) if anal["reconnue"].notna().any() else None),
        "avec_anomalie": (float((anal["nb_anomalies"] > 0).mean()) if len(anal) else None), "anomalies": anomalies,
        "dossiers_acheves": dossiers_acheves(P, a, b),
    }


def dossiers_acheves(P, a, b) -> int:
    """Dossiers dont la dernière pièce a été indexée dans [a, b[ (toutes leurs pièces connues sont indexées)."""
    g = P.dropna(subset=["dossier"]).groupby("dossier")["valide_le"].agg(["max", "count", "size"])
    g = g[g["count"] == g["size"]]
    return int(_entre(g["max"], a, b).sum())


def stock_a(P, t: datetime) -> pd.DataFrame:
    """Pièces en attente d'indexation à l'instant t (analysées ou déposées, non encore validées)."""
    entre = P["entree_stock"].notna() & (P["entree_stock"] < t)
    pas_valide = P["valide_le"].isna() | (P["valide_le"] >= t)
    return P[entre & pas_valide & (P["etat"] != "ERROR")]


def par_indexeur(V, P, a, b, ref_jours: list[date], personnes: dict) -> pd.DataFrame:
    v = V[_entre(V["le"], a, b)]
    if v.empty:
        return pd.DataFrame()
    ind = P[_entre(P["valide_le"], a, b)]
    g = v.groupby("acteur").agg(validations=("id", "size"), premiere=("le", "min"), derniere=("le", "max"),
                                temps_med=("temps_s", "median"), mesures=("mesure", "sum"),
                                dossiers=("piece", lambda s: P.set_index("id").reindex(s)["dossier"].nunique()))
    g["temps_p90"] = v.groupby("acteur")["temps_s"].quantile(.9)
    g["indexees"] = ind.groupby("valide_par").size()
    g["heures"] = v.groupby("acteur").apply(heures_actives, include_groups=False)
    g["cadence"] = g["validations"] / g["heures"].replace(0, np.nan)
    g["corrections"] = ind.groupby("valide_par")["nb_corrections"].mean()
    g["sans_correction"] = ind.groupby("valide_par")["corrigee"].apply(lambda s: float((~s).mean()))
    g["versees"] = P[_entre(P["verse_le"], a, b)].groupby("verse_par").size()
    encours = P[(P["etat"] == "REVIEW_REQUIRED") & P["saisie_humaine"]].groupby("modifie_par").size()
    g["en_cours"] = encours
    if ref_jours:
        r = V[V["le"].dt.date.isin(ref_jours)]
        g["ref"] = r.groupby("acteur").size() / len(ref_jours)
    g = g.fillna({"indexees": 0, "versees": 0, "en_cours": 0})
    g["nom"] = [personnes.get(x) or x for x in g.index]
    g["part"] = g["validations"] / g["validations"].sum()
    return g.sort_values("validations", ascending=False)


def flux_quotidien(P, V, jours: list[date]) -> pd.DataFrame:
    rows = []
    for j in jours:
        a, b = datetime.combine(j, time()), datetime.combine(j + timedelta(days=1), time())
        rows.append({"jour": j, "deposees": int(_entre(P["depose_le"], a, b).sum()),
                     "analysees": int(_entre(P["analyse_le"], a, b).sum()),
                     "indexees": int(_entre(P["valide_le"], a, b).sum()),
                     "versees": int(_entre(P["verse_le"], a, b).sum()),
                     "indexeurs": int(V[_entre(V["le"], a, b)]["acteur"].nunique()),
                     "temps_med": _q(V[_entre(V["le"], a, b)]["temps_s"], .5),
                     "stock_soir": int(len(stock_a(P, b)))})
    return pd.DataFrame(rows)


def matrice_jour_indexeur(V, jours: list[date], personnes: dict) -> pd.DataFrame:
    v = V[V["le"].dt.date.isin(jours)]
    if v.empty:
        return pd.DataFrame()
    m = v.pivot_table(index=v["le"].dt.date, columns="acteur", values="id", aggfunc="size", fill_value=0)
    m = m.reindex(jours, fill_value=0)
    m.columns = [personnes.get(c) or c for c in m.columns]
    return m


def vieillissement(S: pd.DataFrame, t: datetime, cal: Calendrier) -> dict:
    if S.empty:
        return {"<1 j": 0, "1-3 j": 0, "3-7 j": 0, "> 7 j": 0}
    jours = S["entree_stock"].map(lambda d: cal.ouvres_entre(d.date(), t.date()))
    return {"<1 j": int((jours < 1).sum()), "1-3 j": int(((jours >= 1) & (jours < 3)).sum()),
            "3-7 j": int(((jours >= 3) & (jours < 7)).sum()), "> 7 j": int((jours >= 7).sum()),
            "_jours": jours}


# ---------------------------------------------------------------------------
#  Rapport
# ---------------------------------------------------------------------------
@dataclass
class Rapport:
    type: str
    maintenant: datetime
    debut: datetime
    fin: datetime
    libelle: str
    cur: dict
    ref: dict                   # moyenne de référence (jour) ou période précédente (hebdo)
    ref_libelle: str
    indexeurs: pd.DataFrame
    flux: pd.DataFrame
    matrice: pd.DataFrame
    horaire: dict
    stock: dict
    etats: pd.Series
    rubriques: pd.DataFrame
    cumul: dict
    constats: list = field(default_factory=list)
    mode: str = "journal"
    source: str = ""
    pieces: pd.DataFrame | None = None
    validations: pd.DataFrame | None = None
    personnes: dict = field(default_factory=dict)


def _moyenne(dicts: list[dict]) -> dict:
    out = {}
    for k in ("indexees", "versees", "validations", "indexeurs", "deposees", "temps_med", "cadence", "sans_correction",
              "delai_attente_h", "heures"):
        vals = [d[k] for d in dicts if d.get(k) is not None]
        out[k] = float(np.mean(vals)) if vals else None
    return out


def construire(P, E, V, cfg, cal: Calendrier, maintenant: datetime, type_: str, personnes: dict, mode: str) -> Rapport:
    j = maintenant.date()
    if type_ == "hebdo":
        lundi = j - timedelta(days=j.weekday())
        a, b = datetime.combine(lundi, time()), maintenant
        pa, pb = a - timedelta(days=7), b - timedelta(days=7)
        cur, ref = periode(P, E, V, a, b), periode(P, E, V, pa, pb)
        ref_lib, libelle = "semaine précédente", f"Semaine du {lundi:%d/%m} au {j:%d/%m/%Y}"
        ref_jours = [d for d in cal.precedents(lundi, 5)]
    else:
        a, b = datetime.combine(j, time()), maintenant
        actifs = set(V["le"].dt.date)
        ref_jours = [d for d in cal.precedents(j, 60) if d in actifs][:10]
        coupure = maintenant.time()
        refs = [periode(P, E, V, datetime.combine(d, time()), datetime.combine(d, coupure)) for d in ref_jours]
        cur, ref = periode(P, E, V, a, b), _moyenne(refs)
        ref_lib, libelle = f"moyenne des {len(ref_jours)} derniers jours d'indexation", f"Journée du {j:%d/%m/%Y}"
    # tendance : 20 derniers jours ouvrés (jour inclus)
    jours = sorted([j] + cal.precedents(j, 19)) if cal.ouvre(j) else sorted(cal.precedents(j, 20))
    debut_proj = datetime.fromisoformat(cfg["projet"]["date_debut"]).date()
    jours = [d for d in jours if d >= debut_proj] or [j]
    flux = flux_quotidien(P, V, jours)
    semaine = [d for d in jours if d >= a.date()] if type_ == "hebdo" else jours[-10:]
    matrice = matrice_jour_indexeur(V, semaine, personnes)
    # profil horaire du jour
    heures = list(range(cal.debut.hour, cal.fin.hour + 1))
    auj = V[V["le"].dt.date == j]
    prof_ref = V[V["le"].dt.date.isin(ref_jours[:10])] if type_ != "hebdo" else V.iloc[0:0]
    horaire = {"heures": heures, "aujourdhui": [int((auj["le"].dt.hour == h).sum()) for h in heures],
               "reference": [float((prof_ref["le"].dt.hour == h).sum()) / max(1, len(ref_jours[:10])) for h in heures],
               "coupure": maintenant.hour}
    S = stock_a(P, maintenant)
    age = vieillissement(S, maintenant, cal)
    etats = P["etat"].fillna("NON_SUIVI").value_counts()
    # rythme : moyenne des 10 derniers jours ouvrés avec indexation
    rythme_jours = [d for d in cal.precedents(j + timedelta(days=1), 30) if d in set(V["le"].dt.date)][:10]
    rythme = (float(np.mean([int((P["valide_le"].dt.date == d).sum()) for d in rythme_jours])) if rythme_jours else 0.0)
    entree = (float(np.mean([int((P["entree_stock"].dt.date == d).sum()) for d in rythme_jours])) if rythme_jours else 0.0)
    plus_ancien = S["entree_stock"].min() if not S.empty else None
    stock = {"a_indexer": int(len(S)), "age": age, "plus_ancien": plus_ancien, "rythme": rythme, "entree": entree,
             "jours_pour_resorber": (len(S) / (rythme - entree)) if rythme > entree and len(S) else None,
             "date_resorption": (cal.apres(j, math.ceil(len(S) / (rythme - entree))) if rythme > entree and len(S) else None),
             "erreurs": int((P["etat"] == "ERROR").sum()), "a_verser": int(P["etat"].isin(["APPROVED", "FILE_ERROR"]).sum()),
             "a_verser_anciennes": int((P["etat"].isin(["APPROVED", "FILE_ERROR"]) & (P["valide_le"] < maintenant - timedelta(days=1))).sum()),
             "saisies_en_cours": int(((P["etat"] == "REVIEW_REQUIRED") & P["saisie_humaine"]).sum()),
             "non_pris_en_charge": int((P["etat"] == "NON_SUIVI").sum())}
    ind = P[_entre(P["valide_le"], a, b)]
    rub = (ind.assign(rubrique=ind["rubrique"].fillna("—"))
           .groupby("rubrique").agg(pieces=("id", "size"),
                                    chemin=("rubrique_chemin", lambda s: s.dropna().iloc[0] if s.notna().any() else ""))
           .sort_values("pieces", ascending=False))
    total = len(P)
    cumul = {"pieces": total, "indexees": int(P["valide_le"].notna().sum()), "versees": int(P["verse_le"].notna().sum()),
             "dossiers": int(P["dossier"].nunique()),
             "dossiers_acheves": int((P.dropna(subset=["dossier"]).groupby("dossier")["valide_le"].count()
                                      == P.dropna(subset=["dossier"]).groupby("dossier").size()).sum()),
             "indexeurs": int(V["acteur"].nunique()), "temps_med": _q(V["temps_s"], .5)}
    r = Rapport(type_, maintenant, a, b, libelle, cur, ref, ref_lib,
                par_indexeur(V, P, a, b, ref_jours if type_ != "hebdo" else [], personnes),
                flux, matrice, horaire, stock, etats, rub, cumul, mode=mode, pieces=P, validations=V,
                personnes=personnes)
    r.constats = constats(r, cfg)
    return r


def _d(s):
    if s is None:
        return "—"
    s = float(s)
    return f"{s:.0f} s" if s < 90 else f"{int(s // 60)} min {int(s % 60):02d} s"


def constats(r: Rapport, cfg: dict) -> list[dict]:
    """Lecture de l'analyste : critique / attention / ok / info."""
    o, c, ref, st, out = cfg["objectifs"], r.cur, r.ref, r.stock, []
    hebdo = r.type == "hebdo"
    if r.mode == "degrade":
        out.append(("attention", "Mode dégradé", "Le journal du service Scan n'a pas été lu : l'indexeur est le dernier "
                    "modificateur Alfresco et le temps d'indexation est approximatif."))
    if c["validations"] == 0 and not hebdo and r.maintenant.time() > time(10, 0):
        out.append(("critique", "Aucune indexation validée aujourd'hui",
                    f"Aucune validation depuis le début de la journée ; {st['a_indexer']} pièces attendent."))
    obj = o["pieces_indexees_par_jour"] * (5 if hebdo else 1)
    if c["indexees"] and ref.get("indexees") is not None and significatif(c["indexees"], ref["indexees"]):
        sens = "au-dessus" if c["indexees"] > ref["indexees"] else "en dessous"
        out.append(("ok" if sens == "au-dessus" else "attention", f"Volume {sens} de la référence",
                    f"{c['indexees']} pièces indexées contre {ref['indexees']:.0f} ({r.ref_libelle})."))
    if not hebdo and r.maintenant.time() >= time(17, 0):
        out.append(("ok" if c["indexees"] >= obj else "attention", "Objectif quotidien",
                    f"{c['indexees']} pièces indexées pour un objectif de {obj}."))
    if st["a_indexer"]:
        if st["date_resorption"]:
            out.append(("info", "Résorption du stock",
                        f"{st['a_indexer']} pièces à indexer. Au rythme des 10 derniers jours ({st['rythme']:.0f} indexées "
                        f"/ {st['entree']:.0f} nouvelles par jour), le stock serait résorbé vers le {st['date_resorption']:%d/%m/%Y}."))
        elif st["rythme"] or st["entree"]:
            out.append(("attention", "Le stock à indexer augmente",
                        f"Il entre {st['entree']:.0f} pièces par jour pour {st['rythme']:.0f} indexées : "
                        f"{st['a_indexer']} pièces attendent. Renforcer l'équipe d'indexation ou la priorisation."))
    vieux = sum(v for k, v in st["age"].items() if k in ("3-7 j", "> 7 j"))
    if vieux:
        out.append(("attention" if st["age"]["> 7 j"] == 0 else "critique", "Pièces anciennes en attente",
                    f"{vieux} pièces attendent depuis plus de 3 jours ouvrés (dont {st['age']['> 7 j']} depuis plus de 7). "
                    f"La plus ancienne est entrée le {st['plus_ancien']:%d/%m à %H:%M}."))
    t = c["temps_med"]
    if t is not None:
        lvl = "ok" if t <= o["temps_cible_s"] else "attention"
        out.append((lvl, "Temps d'indexation", f"Temps médian par pièce : {_d(t)} (90 % des pièces en moins de {_d(c['temps_p90'])}), "
                    f"pour une cible de {_d(o['temps_cible_s'])}. Mesuré sur {c['temps_mesures']} enchaînements."))
    I = r.indexeurs
    if not I.empty and t:
        lents = I[(I["mesures"] >= 20) & (I["temps_med"] > 1.5 * t)]
        for _, x in lents.iterrows():
            out.append(("info", f"{x['nom']} : temps élevé", f"Temps médian {_d(x['temps_med'])} contre {_d(t)} pour l'équipe "
                        "— pièces plus complexes ou besoin d'accompagnement."))
    if c["sans_correction"] is not None and c["indexees"] >= 20:
        taux_corr = 1 - c["sans_correction"]
        top = ", ".join(LIBELLES_CHAMPS.get(k, k) for k in list(c["champs"].index[:3]))
        out.append(("attention" if taux_corr > o["seuil_correction"] else "info", "Qualité des propositions automatiques",
                    f"{taux_corr * 100:.0f} % des pièces indexées ont été corrigées par l'indexeur"
                    + (f" ; champs les plus corrigés : {top}." if top else ".")))
    if c["en_lot"]:
        out.append(("info", "Validations en lot", f"{c['en_lot']} validations à moins de {cfg['mesure']['temps_min_s']} s "
                    "de la précédente : non retenues dans le temps d'indexation."))
    if st["erreurs"]:
        out.append(("attention", "Analyses en échec", f"{st['erreurs']} pièces en échec d'analyse : relancer depuis Share ou "
                    "vérifier le fichier (taille, pages)."))
    if st["a_verser_anciennes"]:
        out.append(("attention", "Pièces indexées non versées", f"{st['a_verser_anciennes']} pièces validées depuis plus d'un "
                    "jour n'ont pas été versées aux archives."))
    if st["non_pris_en_charge"]:
        out.append(("info", "Dépôts non pris en charge", f"{st['non_pris_en_charge']} pièces du dossier Scan n'ont pas encore "
                    "été vues par le service d'analyse."))
    ordre = {"critique": 0, "attention": 1, "ok": 2, "info": 3}
    return [{"niveau": n, "titre": ti, "texte": tx} for n, ti, tx in sorted(out, key=lambda x: ordre[x[0]])]
