"""Indicateurs de l'indexation (voir docs/INDICATEURS.md pour les définitions).

Grains :
  pièce indexée     = pièce dont les métadonnées ont été ENREGISTRÉES par une personne (« Enregistrer » dans
                      Share) ; comptée une fois, à son PREMIER enregistrement. Un enregistrement suivant est une reprise.
                      Date et auteur : historique des versions Alfresco ; à défaut dernière modification ; à défaut
                      validation (qui exige un enregistrement préalable).
  pièce validée     = événement METADATA_APPROVED du service Scan (contrôle de l'archiviste)
  indexeur          = auteur de l'enregistrement (identifiant Alfresco ; nom lu dans le profil Alfresco)
  temps d'indexation = écart entre deux indexations consécutives d'un même indexeur, s'il est compris entre
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
def preparer(pieces: list[dict], evenements: list[dict], personnes: dict, cfg: dict, agent: list[dict] | None = None):
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
    P["verse_le"] = premier("RECORD_FILED")
    P["verse_par"] = premier("RECORD_FILED", col="acteur")

    # Événements d'indexation : enregistrements par une personne, complétés par les validations
    # sans enregistrement visible avant elles (la validation exige un enregistrement préalable).
    # AGENT_INDEXED : enregistrement de l'agent d'indexation IA, daté au journal du service Scan
    S = E[E["evenement"].isin(["METADATA_SAVED", "AGENT_INDEXED"]) & E["humain"]].copy()
    S["source"] = [("journal_agent" if ev == "AGENT_INDEXED" else _j(d).get("source", "version"))
                   for ev, d in zip(S["evenement"], S["details"])]
    A = E[(E["evenement"] == "METADATA_APPROVED") & E["humain"]]
    prem_save = S.groupby("piece")["le"].min()
    deja = pd.to_datetime(A["piece"].map(prem_save.to_dict()), errors="coerce")
    a_sans = A[~(deja <= A["le"])]
    a_sans = a_sans.sort_values(["le", "id"]).drop_duplicates("piece")
    X = pd.concat([S, a_sans.assign(evenement="METADATA_SAVED", source="validation")], ignore_index=True)
    X = X.sort_values(["le", "id"])
    P["indexe_le"] = X.groupby("piece")["le"].first()
    P["indexe_par"] = X.groupby("piece")["acteur"].first()
    P["enregistrements"] = X.groupby("piece").size()
    for c in ("echecs_analyse", "relances", "validations", "enregistrements"):
        P[c] = P[c].fillna(0).astype(int)
    # Une pièce dont l'analyse n'a laissé aucune trace d'événement (anciennes versions) : date de dépôt
    P["entree_stock"] = P["analyse_le"].fillna(P["depose_le"])
    P["champs_corriges"] = [corrections(a, b) for a, b in zip(P["proposition"].fillna(""), P["validation"].fillna(""))]
    P["nb_corrections"] = P["champs_corriges"].map(len)
    P["corrigee"] = P["nb_corrections"] > 0
    P["reconnue"] = P["nature_reconnue"].map(lambda x: None if x in (None, "") or pd.isna(x) else x != "inconnu")
    P["nb_anomalies"] = P["anomalies"].fillna("[]").map(lambda x: len(json.loads(x)) if x else 0)
    P = P.reset_index()

    # Temps d'indexation : écart avec l'indexation précédente du même indexeur, le même jour.
    # Plusieurs enregistrements successifs d'une même pièce par la même personne = une seule indexation,
    # terminée à son DERNIER enregistrement (corrections comprises).
    m = cfg["mesure"]
    V = X.sort_values(["acteur", "le", "id"]).copy()
    suivi = V["piece"].eq(V["piece"].shift(-1)) & V["acteur"].eq(V["acteur"].shift(-1)) \
        & ((V["le"].shift(-1) - V["le"]) < pd.Timedelta(minutes=m["session_max_min"]))
    V = V[~suivi].copy()
    V["jour"] = V["le"].dt.date
    V["ecart_s"] = V.groupby(["acteur", "jour"])["le"].diff().dt.total_seconds()
    V["mesure"] = V["ecart_s"].between(m["temps_min_s"], m["session_max_min"] * 60)
    V["temps_s"] = V["ecart_s"].where(V["mesure"])
    V["en_lot"] = V["ecart_s"] < m["temps_min_s"]
    ordre = V.sort_values(["le", "id"])
    V["premiere"] = ~ordre["piece"].duplicated(keep="first").reindex(V.index)
    V["nom"] = V["acteur"].map(lambda a: personnes.get(a) or a)
    # Agent IA : temps de traitement réel de chaque pièce (OCR + modèle), noté par l'agent lui-même
    agents = set((cfg["perimetre"].get("agents_ia") or {}).keys())
    V["agent"] = V["acteur"].isin(agents)
    duree = {}
    for a in agent or []:
        try:
            if a.get("outcome") == "FILLED" and a.get("total_ms"):
                duree[a["node_id"]] = float(a["total_ms"]) / 1000
        except (TypeError, ValueError):
            continue
    G = pd.DataFrame(agent or [], columns=["node_id", "outcome", "attempts", "fields", "ocr_ms", "model_ms", "total_ms",
                                            "model", "indexed_at", "error"])
    G["le"] = pd.to_datetime(G["indexed_at"], errors="coerce", utc=True).dt.tz_convert(
        cfg["projet"]["fuseau_horaire"]).dt.tz_localize(None)
    G["total_s"] = pd.to_numeric(G["total_ms"], errors="coerce") / 1000
    G["champs"] = G["fields"].map(lambda f: {k: v for k, v in _j(f).items() if str(k).startswith("mulcv:")})
    V.attrs["agent"] = G
    if duree:
        t = V["piece"].map(duree)
        sel = V["agent"] & t.notna()
        V.loc[sel, "temps_s"] = t[sel]
        V.loc[sel, "mesure"] = True
        V.loc[sel, "en_lot"] = False
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
    ind = P[_entre(P["indexe_le"], a, b)]
    val = P[_entre(P["valide_le"], a, b)]
    v = V[_entre(V["le"], a, b)]
    va = v[v["agent"]]
    vh = v[~v["agent"]]          # temps, heures et cadence : indexeurs humains
    vers = P[_entre(P["verse_le"], a, b)]
    anal = P[_entre(P["analyse_le"], a, b)]
    h = heures_actives(vh)
    delai_dep = (ind["indexe_le"] - ind["depose_le"]).dt.total_seconds() / 3600
    delai_att = (ind["indexe_le"] - ind["entree_stock"]).dt.total_seconds() / 3600
    delai_val = (val["valide_le"] - val["indexe_le"]).dt.total_seconds() / 60
    delai_ver = (vers["verse_le"] - vers["valide_le"]).dt.total_seconds() / 60
    champs = pd.Series([c for l in val["champs_corriges"] for c in l], dtype=object).value_counts()
    anomalies = pd.Series([w for x in anal["anomalies"].fillna("[]") for w in json.loads(x)], dtype=object)
    anomalies = anomalies.map(lambda w: "Valeurs en conflit" if str(w).startswith("CONFLICT_") else w).value_counts()
    return {
        "indexees": int(len(ind)), "reprises": int((~v["premiere"]).sum()), "validations": int(len(v)),
        "validees": int(len(val)), "delai_validation_min": _q(delai_val, .5),
        "sources": v["source"].value_counts().to_dict(),
        "indexeurs": int(vh["acteur"].nunique()), "versees": int(len(vers)),
        "par_agent": int(ind["indexe_par"].isin(set(va["acteur"])).sum()) if len(va) else 0,
        "agent_indexations": int(len(va)), "agent_temps_med": _q(va["temps_s"], .5),
        "deposees": int(_entre(P["depose_le"], a, b).sum()), "analysees": int(len(anal)),
        "echecs_analyse": int(((E["evenement"] == "ANALYSIS_FAILED") & _entre(E["le"], a, b)).sum()),
        "relances": int(((E["evenement"] == "ANALYSIS_REQUESTED") & E["humain"] & _entre(E["le"], a, b)).sum()),
        "temps_med": _q(vh["temps_s"], .5), "temps_p90": _q(vh["temps_s"], .9),
        "temps_moy": (float(vh["temps_s"].mean()) if vh["temps_s"].notna().any() else None),
        "temps_mesures": int(vh["mesure"].sum()), "en_lot": int(vh["en_lot"].sum()),
        "heures": h, "cadence": (len(vh) / h) if h > 0 else None,
        "delai_depot_h": _q(delai_dep, .5), "delai_attente_h": _q(delai_att, .5), "delai_attente_p90_h": _q(delai_att, .9),
        "delai_versement_min": _q(delai_ver, .5),
        "sans_correction": (float((~val["corrigee"]).mean()) if len(val) else None),
        "corrections_moy": (float(val["nb_corrections"].mean()) if len(val) else None),
        "champs": champs, "reconnaissance": (float(anal["reconnue"].dropna().mean()) if anal["reconnue"].notna().any() else None),
        "avec_anomalie": (float((anal["nb_anomalies"] > 0).mean()) if len(anal) else None), "anomalies": anomalies,
        "dossiers_acheves": dossiers_acheves(P, a, b),
    }


def dossiers_acheves(P, a, b) -> int:
    """Dossiers dont la dernière pièce a été indexée dans [a, b[ (toutes leurs pièces connues sont indexées)."""
    g = P.dropna(subset=["dossier"]).groupby("dossier")["indexe_le"].agg(["max", "count", "size"])
    g = g[g["count"] == g["size"]]
    return int(_entre(g["max"], a, b).sum())


def stock_a(P, t: datetime) -> pd.DataFrame:
    """Pièces en attente d'indexation à l'instant t (analysées ou déposées, non encore validées)."""
    entre = P["entree_stock"].notna() & (P["entree_stock"] < t)
    pas_indexe = P["indexe_le"].isna() | (P["indexe_le"] >= t)
    return P[entre & pas_indexe & (P["etat"] != "ERROR")]


def par_indexeur(V, P, a, b, ref_jours: list[date], personnes: dict) -> pd.DataFrame:
    v = V[_entre(V["le"], a, b)]
    if v.empty:
        return pd.DataFrame()
    ind = P[_entre(P["indexe_le"], a, b)]
    val = P[_entre(P["valide_le"], a, b)]
    g = v.groupby("acteur").agg(validations=("id", "size"), premiere=("le", "min"), derniere=("le", "max"),
                                temps_med=("temps_s", "median"), mesures=("mesure", "sum"),
                                dossiers=("piece", lambda s: P.set_index("id").reindex(s)["dossier"].nunique()))
    g["temps_p90"] = v.groupby("acteur")["temps_s"].quantile(.9)
    g["indexees"] = ind.groupby("indexe_par").size()
    g["validees"] = val.groupby("valide_par").size()
    g["heures"] = v.groupby("acteur").apply(heures_actives, include_groups=False)
    g["agent"] = v.groupby("acteur")["agent"].first()
    g["cadence"] = g["validations"] / g["heures"].replace(0, np.nan)
    g["corrections"] = val.groupby("valide_par")["nb_corrections"].mean()
    g["sans_correction"] = val.groupby("valide_par")["corrigee"].apply(lambda s: float((~s).mean()))
    g["versees"] = P[_entre(P["verse_le"], a, b)].groupby("verse_par").size()
    g["en_cours"] = P[P["indexe_le"].notna() & P["valide_le"].isna()].groupby("indexe_par").size()
    if ref_jours:
        r = V[V["le"].dt.date.isin(ref_jours)]
        g["ref"] = r.groupby("acteur").size() / len(ref_jours)
    g = g.fillna({"indexees": 0, "validees": 0, "versees": 0, "en_cours": 0})
    g["nom"] = [personnes.get(x) or x for x in g.index]
    g["part"] = g["validations"] / g["validations"].sum()
    return g.sort_values("validations", ascending=False)


def flux_quotidien(P, V, jours: list[date]) -> pd.DataFrame:
    rows = []
    for j in jours:
        a, b = datetime.combine(j, time()), datetime.combine(j + timedelta(days=1), time())
        rows.append({"jour": j, "deposees": int(_entre(P["depose_le"], a, b).sum()),
                     "analysees": int(_entre(P["analyse_le"], a, b).sum()),
                     "indexees": int(_entre(P["indexe_le"], a, b).sum()),
                     "validees": int(_entre(P["valide_le"], a, b).sum()),
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
RESULTATS_AGENT = {
    "FILLED": "Pièces remplies et enregistrées", "NOTHING_FOUND": "Lues, rien de sûr à remplir",
    "SKIPPED_HUMAN": "Laissées : déjà touchées par une personne", "NO_PERMISSION": "Refusées : pas le droit d'écrire",
    "TOO_LARGE": "Fichier trop volumineux", "ERROR": "Erreurs",
}


def par_boite(P, V, a, b) -> pd.DataFrame:
    """Par carton (boîte) : pièces, indexées par les humains et par l'agent (total et sur la période), restant."""
    agents = set(V.loc[V["agent"], "acteur"])
    X = P.dropna(subset=["carton"]).copy()
    X["ia"] = X["indexe_par"].isin(agents)
    X["hum"] = X["indexe_le"].notna() & ~X["ia"]
    X["per"] = _entre(X["indexe_le"], a, b)
    g = X.groupby("carton").agg(conteneur=("conteneur", "first"), pieces=("id", "size"),
                                humains=("hum", "sum"), agent=("ia", "sum"),
                                humains_periode=("hum", lambda s: int((s & X.loc[s.index, "per"]).sum())),
                                agent_periode=("ia", lambda s: int((s & X.loc[s.index, "per"]).sum())),
                                derniere=("indexe_le", "max"))
    g["indexees"] = g["humains"] + g["agent"]
    g["restant"] = g["pieces"] - g["indexees"]
    g["avancement"] = g["indexees"] / g["pieces"]
    return g.sort_values(["derniere"], ascending=False, na_position="last")


def duel(P, V, a, b) -> dict:
    """Humains vs agent IA sur [a, b[ : volume, vitesse, ce que l'archiviste garde de leur travail."""
    G = V.attrs.get("agent")
    G = G if G is not None else pd.DataFrame(columns=["node_id", "outcome", "le", "total_s", "champs"])
    v = V[_entre(V["le"], a, b)]
    vh, va = v[~v["agent"]], v[v["agent"]]
    agents = set(V.loc[V["agent"], "acteur"]) | set(G.get("acteur", []))
    ind = P[_entre(P["indexe_le"], a, b)]
    ind_h = ind[~ind["indexe_par"].isin(agents) & ind["indexe_par"].notna()]
    g = G[_entre(G["le"], a, b)]
    remplies = g[g["outcome"] == "FILLED"]
    h = heures_actives(vh)
    # contrôle de l'archiviste sur les pièces remplies par l'agent (validées pendant la période)
    snap = P.set_index("id")
    gardes = corriges = 0
    val_agent = 0
    for _, x in G[G["outcome"] == "FILLED"].iterrows():
        if x["node_id"] not in snap.index:
            continue
        p = snap.loc[x["node_id"]]
        if pd.isna(p["valide_le"]) or not (a <= p["valide_le"] < b):
            continue
        val_agent += 1
        v_ok = _j(p["validation"])
        for k, val in x["champs"].items():
            if str(val).strip() == str(v_ok.get(k, "")).strip():
                gardes += 1
            else:
                corriges += 1
    val_h = P[_entre(P["valide_le"], a, b) & P["indexe_par"].notna() & ~P["indexe_par"].isin(agents)]
    return {
        "h_pieces": int(len(ind_h)), "h_indexeurs": int(vh["acteur"].nunique()), "h_temps": _q(vh["temps_s"], .5),
        "h_cadence": (len(vh) / h) if h > 0 else None, "h_validees": int(len(val_h)),
        "h_sans_correction": (float((~val_h["corrigee"]).mean()) if len(val_h) else None),
        "a_examinees": int(len(g)), "a_pieces": int(len(remplies)), "a_temps": _q(remplies["total_s"], .5),
        "a_cadence": (3600 / _q(remplies["total_s"], .5)) if len(remplies) and _q(remplies["total_s"], .5) else None,
        "a_champs": (float(remplies["champs"].map(len).mean()) if len(remplies) else None),
        "a_validees": val_agent, "a_exactitude": (gardes / (gardes + corriges)) if gardes + corriges else None,
        "a_gardes": gardes, "a_corriges": corriges,
        "a_resultats": g["outcome"].value_counts().to_dict(),
        "a_erreurs": (g.loc[g["outcome"] == "ERROR", "error"].fillna("").astype(str).str.slice(0, 110).value_counts().head(3).to_dict()
                      if "error" in g else {}),
        "a_total": int((G["outcome"] == "FILLED").sum()), "a_total_examinees": int(len(G)),
        "a_resultats_total": G["outcome"].value_counts().to_dict(),
        "actif": bool(len(G)) or bool(len(va)),
    }


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
    duel: dict = field(default_factory=dict)
    boites: pd.DataFrame | None = None


def _moyenne(dicts: list[dict]) -> dict:
    out = {}
    for k in ("indexees", "validees", "versees", "validations", "indexeurs", "deposees", "temps_med", "cadence", "sans_correction",
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
    hum = P.loc[P["indexe_par"].notna() & ~P["indexe_par"].isin(set(V.loc[V["agent"], "acteur"])), "indexe_le"]
    debut_proj = hum.min().date() if hum.notna().any() else datetime.fromisoformat(cfg["projet"]["date_debut"]).date()
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
    rythme = (float(np.mean([int((P["indexe_le"].dt.date == d).sum()) for d in rythme_jours])) if rythme_jours else 0.0)
    entree = (float(np.mean([int((P["entree_stock"].dt.date == d).sum()) for d in rythme_jours])) if rythme_jours else 0.0)
    plus_ancien = S["entree_stock"].min() if not S.empty else None
    stock = {"a_indexer": int(len(S)), "age": age, "plus_ancien": plus_ancien, "rythme": rythme, "entree": entree,
             "jours_pour_resorber": (len(S) / (rythme - entree)) if rythme > entree and len(S) else None,
             "date_resorption": (cal.apres(j, math.ceil(len(S) / (rythme - entree))) if rythme > entree and len(S) else None),
             "erreurs": int((P["etat"] == "ERROR").sum()), "a_verser": int(P["etat"].isin(["APPROVED", "FILE_ERROR"]).sum()),
             "a_verser_anciennes": int((P["etat"].isin(["APPROVED", "FILE_ERROR"]) & (P["valide_le"] < maintenant - timedelta(days=1))).sum()),
             "saisies_en_cours": int((P["indexe_le"].notna() & P["valide_le"].isna()).sum()),
             "non_pris_en_charge": int((P["etat"] == "NON_SUIVI").sum())}
    ind = P[_entre(P["indexe_le"], a, b)]
    rub = (ind.assign(rubrique=ind["rubrique"].fillna("—"))
           .groupby("rubrique").agg(pieces=("id", "size"),
                                    chemin=("rubrique_chemin", lambda s: s.dropna().iloc[0] if s.notna().any() else ""))
           .sort_values("pieces", ascending=False))
    total = len(P)
    cumul = {"par_agent": int(P["indexe_par"].isin(set(V.loc[V["agent"], "acteur"])).sum()), "pieces": total, "indexees": int(P["indexe_le"].notna().sum()), "validees": int(P["valide_le"].notna().sum()),
             "versees": int(P["verse_le"].notna().sum()),
             "dossiers": int(P["dossier"].nunique()),
             "dossiers_acheves": int((P.dropna(subset=["dossier"]).groupby("dossier")["indexe_le"].count()
                                      == P.dropna(subset=["dossier"]).groupby("dossier").size()).sum()),
             "indexeurs": int(V["acteur"].nunique()), "temps_med": _q(V["temps_s"], .5)}
    cumul["debut"] = debut_proj
    premiers = P.loc[P["indexe_le"].notna() & ~P["indexe_par"].isin(set(V.loc[V["agent"], "acteur"]))].sort_values("indexe_le")
    cumul["premier_indexeur"] = personnes.get(premiers["indexe_par"].iloc[0], premiers["indexe_par"].iloc[0]) if len(premiers) else None
    cumul["indexeurs"] = int(V.loc[~V["agent"], "acteur"].nunique())
    cumul["temps_med"] = _q(V.loc[~V["agent"], "temps_s"], .5)
    r = Rapport(type_, maintenant, a, b, libelle, cur, ref, ref_lib,
                par_indexeur(V, P, a, b, ref_jours if type_ != "hebdo" else [], personnes),
                flux, matrice, horaire, stock, etats, rub, cumul, mode=mode, pieces=P, validations=V,
                personnes=personnes)
    r.duel = duel(P, V, a, b)
    r.boites = par_boite(P, V, a, b)
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
        out.append(("critique", "Aucune indexation aujourd'hui",
                    f"Aucun enregistrement de métadonnées depuis le début de la journée ; {st['a_indexer']} pièces attendent."))
    src = c.get("sources") or {}
    approx = src.get("validation", 0) + src.get("derniere_modification", 0)
    if c["validations"] and approx / c["validations"] > .5:
        out.append(("info", "Datation des enregistrements",
                    f"{approx * 100 / c['validations']:.0f} % des indexations sont datées par la validation ou la dernière "
                    "modification, faute d'historique des versions : temps par pièce approximatif."))
    if st["saisies_en_cours"]:
        out.append(("info", "Indexées, pas encore validées",
                    f"{st['saisies_en_cours']} pièces ont leurs métadonnées enregistrées mais pas encore validées par l'archiviste."))
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
    d = r.duel or {}
    res = d.get("a_resultats") or {}
    if d.get("a_examinees") and res.get("NO_PERMISSION", 0) >= .5 * d["a_examinees"]:
        out.append(("critique", "L'agent IA n'a pas le droit d'écrire",
                    f"{res['NO_PERMISSION']} des {d['a_examinees']} pièces examinées par l'agent ont été refusées (droit de "
                    "modification). Ajouter service-account-ceiba-ai-agent comme Collaborateur du site mulcv."))
    elif d.get("a_examinees") and d.get("a_pieces", 0) == 0:
        principal = max(res, key=res.get)
        err = next(iter(d.get("a_erreurs") or {}), "")
        out.append(("critique" if principal == "ERROR" else "attention", "L'agent IA n'a rempli aucune pièce",
                    f"{d['a_examinees']} pièces examinées, aucune enregistrée ; résultat le plus fréquent : "
                    f"{RESULTATS_AGENT.get(principal, principal).lower()} ({res[principal]})"
                    + (f" — « {err} »." if principal == "ERROR" and err else ".")))
    elif d.get("actif") and not d.get("a_examinees"):
        out.append(("attention", "Agent IA à l'arrêt", "L'agent IA n'a examiné aucune pièce sur la période "
                    "(désactivé, hors de ses horaires ou limite quotidienne atteinte)."))
    if c.get("agent_indexations"):
        out.append(("info", "Agent IA d'indexation",
                    f"L'agent IA a indexé {c['agent_indexations']} pièces ({c['par_agent']} en première indexation), "
                    f"en {_d(c['agent_temps_med'])} médian par pièce. Ses pièces restent à vérifier et valider par un archiviste ; "
                    "il est compté comme un indexeur mais exclu du temps médian de l'équipe."))
    I = r.indexeurs
    if not I.empty and t:
        lents = I[(I["mesures"] >= 20) & (I["temps_med"] > 1.5 * t) & ~I["agent"].fillna(False).astype(bool)]
        for _, x in lents.iterrows():
            out.append(("info", f"{x['nom']} : temps élevé", f"Temps médian {_d(x['temps_med'])} contre {_d(t)} pour l'équipe "
                        "— pièces plus complexes ou besoin d'accompagnement."))
    if c["sans_correction"] is not None and c["validees"] >= 20:
        taux_corr = 1 - c["sans_correction"]
        top = ", ".join(LIBELLES_CHAMPS.get(k, k) for k in list(c["champs"].index[:3]))
        out.append(("attention" if taux_corr > o["seuil_correction"] else "info", "Qualité des propositions automatiques",
                    f"{taux_corr * 100:.0f} % des pièces validées ont été corrigées par l'indexeur"
                    + (f" ; champs les plus corrigés : {top}." if top else ".")))
    if c["en_lot"]:
        out.append(("info", "Indexations en lot", f"{c['en_lot']} indexations à moins de {cfg['mesure']['temps_min_s']} s "
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
