"""Collecte de l'activité d'indexation dans le SAE MULCV.

Deux sources, lues sur le serveur du SAE, toutes deux en lecture seule :

1. Le journal du service Scan (Ceibatech/MULCV-alfresco, services/scan) — base H2 `/data/scan.mv.db`
   du conteneur `mulcv-scan`. C'est la source de vérité du parcours d'indexation :
     scan_job   : état courant de chaque pièce (QUEUED, ANALYSING, REVIEW_REQUIRED, ERROR, APPROVED,
                  DECLARING, FILING, FILE_ERROR, ARCHIVED), valeurs proposées par l'analyse (applied),
                  valeurs validées par l'indexeur (approved_snapshot), validateur (approved_by)
     scan_audit : événements horodatés et signés
                  ANALYSIS_COMPLETED / ANALYSIS_FAILED   (service-account-mulcv-scan-worker)
                  ANALYSIS_REQUESTED                     (relance demandée par un utilisateur)
                  METADATA_APPROVED                      (validation de l'indexation par l'indexeur)
                  RECORD_FILED                           (versement aux archives, record RM)
                  SEPARATOR_REMOVED …                    (maintenance, ignorés ici)
   La base est en cours d'utilisation par le service : on en lit une COPIE (docker cp), ouverte par le
   pilote H2 embarqué dans l'image du service (même version que le fichier), dans un conteneur jetable
   sans réseau. Le service Scan n'est ni arrêté ni modifié.

2. La base PostgreSQL d'Alfresco (comme numerisation-stats) : propriétés mulcv:* des pièces, dates et
   auteurs Alfresco, arborescence Scan/<…>/<conteneur>/<carton>/<dossier>, noms des utilisateurs.
   Transaction `default_transaction_read_only=on`.

Sans journal Scan (source.journal_scan: false), un mode dégradé lit seulement Alfresco : une pièce est
indexée si mulcv:qualityStatus = VERIFIED, par cm:modifier à cm:modified (approximation signalée).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import random
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

NS_MULCV = "https://ceiba-analytics.com/model/mulcv/1.0"
PROPS = ["dossierId", "boxReference", "typeDocumentCode", "nature", "classId", "classCode", "classPath",
         "planVersion", "classificationStatus", "qualityStatus", "ocrStatus", "sourceSystem", "subject"]
COMPTE_WORKER = "service-account-mulcv-scan-worker"


# ---------------------------------------------------------------------------
#  Outils
# ---------------------------------------------------------------------------
def _docker(*args, entree: bytes | None = None, timeout=600) -> bytes:
    r = subprocess.run(["docker", *args], input=entree, capture_output=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"docker {' '.join(args[:3])}… : {r.stderr.decode(errors='replace').strip()[:400]}")
    return r.stdout


def _dt(v, tz: ZoneInfo) -> datetime | None:
    """ISO (UTC 'Z' du journal Scan, ou horodatage Alfresco) -> heure locale naïve."""
    if v in (None, ""):
        return None
    s = str(v).strip().replace("Z", "+00:00").replace("+0000", "+00:00")
    d = datetime.fromisoformat(s)
    return d.astimezone(tz).replace(tzinfo=None) if d.tzinfo else d


def _decode(nom: str | None) -> str | None:
    """Noms d'association Alfresco encodés ISO 9075 (_x0020_ = espace)."""
    if not nom:
        return nom
    return re.sub(r"_x([0-9A-Fa-f]{4})_", lambda m: chr(int(m.group(1), 16)), nom)


def _json(x) -> dict:
    if not x:
        return {}
    try:
        v = json.loads(x)
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def decouper_chemin(dossiers: list[str], racine_production: str = "Production") -> dict:
    """Dossiers parents d'une pièce (du dossier Scan vers la pièce) -> zone, conteneur, carton, dossier.
    Arborescence de production : Scan/Production/<zone>/<conteneur>/<carton>/<dossier>/<pièce>."""
    d = [x for x in dossiers if x]
    zone = None
    if racine_production in d:
        i = d.index(racine_production)
        zone = d[i + 1] if len(d) > i + 1 else None
        d = d[i + 2:]
    return {"zone": zone,
            "conteneur": d[-3] if len(d) >= 3 else None,
            "carton": d[-2] if len(d) >= 2 else None,
            "dossier": d[-1] if len(d) >= 1 else None}


# ---------------------------------------------------------------------------
#  1. Journal du service Scan (H2)
# ---------------------------------------------------------------------------
EXPORT_SQL = """CALL CSVWRITE('/w/job.csv', 'SELECT node_id, status, attempts, updated_at, analysis, applied, error,
  approved_by, record_id, approved_snapshot FROM scan_job', 'charset=UTF-8 fieldSeparator=;');
CALL CSVWRITE('/w/audit.csv', 'SELECT id, node_id, event, actor, at_time, details FROM scan_audit ORDER BY id',
  'charset=UTF-8 fieldSeparator=;');
"""
# Table de l'agent d'indexation IA (absente sur les versions du service Scan sans agent : lecture facultative)
EXPORT_AGENT_SQL = """CALL CSVWRITE('/w/agent.csv', 'SELECT node_id, outcome, attempts, fields, ocr_ms, model_ms, total_ms, model,
  indexed_at, error FROM agent_indexing', 'charset=UTF-8 fieldSeparator=;');
"""


class JournalScan:
    """Lit une copie du journal H2 du service Scan."""

    def __init__(self, c: dict):
        self.c = c or {}

    def _conteneur(self) -> str:
        if self.c.get("conteneur"):
            return self.c["conteneur"]
        for ligne in _docker("ps", "--format", "{{.Names}}\t{{.Image}}").decode().splitlines():
            nom, image = (ligne.split("\t") + [""])[:2]
            if re.search(r"mulcv-scan", nom + image, re.I):
                return nom
        raise RuntimeError("Conteneur du service Scan (image mulcv-scan) introuvable sur ce serveur ; "
                           "le préciser dans config.yaml : source.journal_scan.conteneur")

    def lire(self) -> tuple[list[dict], list[dict], list[dict]]:
        ctn = self._conteneur()
        image = _docker("inspect", "-f", "{{.Config.Image}}", ctn).decode().strip()
        env = dict(l.split("=", 1) for l in _docker("inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", ctn)
                   .decode().splitlines() if "=" in l)
        mdp = self.c.get("mot_de_passe") or env.get("SCAN_DB_PASSWORD", "")
        fichier = self.c.get("fichier", "/data/scan.mv.db")
        # Dossier de travail hors de /tmp : sous systemd, /tmp peut être privé au service et invisible
        # du démon Docker, qui monterait alors un dossier vide dans le conteneur de lecture.
        travail = Path(self.c.get("dossier_travail") or Path(__file__).resolve().parent.parent / "data" / "tmp")
        travail.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="indexation-h2-", dir=travail) as tmp:
            w = Path(tmp)
            w.chmod(0o777)  # l'image tourne sous l'utilisateur 10001
            _docker("cp", f"{ctn}:{fichier}", str(w / "scan.mv.db"))
            _docker("cp", f"{ctn}:{self.c.get('jar', '/app/app.jar')}", str(w / "app.jar"))
            with zipfile.ZipFile(w / "app.jar") as z:
                h2 = [n for n in z.namelist() if re.search(r"(^|/)h2-[\d.]+\.jar$", n)]
                if not h2:
                    raise RuntimeError("Pilote H2 introuvable dans l'application du service Scan")
                (w / "h2.jar").write_bytes(z.read(h2[0]))
            (w / "app.jar").unlink()
            (w / "export.sql").write_text(EXPORT_SQL, encoding="utf-8")
            (w / "agent.sql").write_text(EXPORT_AGENT_SQL, encoding="utf-8")
            for f in w.iterdir():
                f.chmod(0o666)

            def executer(script):
                _docker("run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp", "-v", f"{w}:/w",
                        "-e", f"H2PW={mdp}", "--entrypoint", "sh", image, "-c",
                        'exec java -cp /w/h2.jar org.h2.tools.RunScript -url "jdbc:h2:file:/w/scan" '
                        f'-user sa -password "$H2PW" -script /w/{script}', timeout=300)
            executer("export.sql")
            if not (w / "job.csv").exists() or not (w / "audit.csv").exists():
                raise RuntimeError(f"Export du journal Scan introuvable dans {w} : le dossier n'est pas partagé avec Docker")
            try:
                executer("agent.sql")
                agent = lire_csv(w / "agent.csv")
            except RuntimeError as e:
                log.info("Table agent_indexing absente ou illisible (%s) : agent IA suivi par le journal seul", str(e)[:120])
                agent = []
            return lire_csv(w / "job.csv"), lire_csv(w / "audit.csv"), agent


def lire_csv(chemin: Path) -> list[dict]:
    texte = chemin.read_text(encoding="utf-8")
    return [{k.lower(): v for k, v in r.items()} for r in csv.DictReader(io.StringIO(texte), delimiter=";")]


# ---------------------------------------------------------------------------
#  2. Base PostgreSQL d'Alfresco
# ---------------------------------------------------------------------------
SQL_PIECES = """WITH RECURSIVE racine AS (
  SELECT n.id FROM alf_node n JOIN alf_store s ON s.id = n.store_id
  WHERE s.protocol = 'workspace' AND s.identifier = 'SpacesStore' AND n.uuid = '{racine}'
), arbre AS (
  SELECT id, 0 AS niveau, ARRAY[]::text[] AS chemin FROM racine
  UNION ALL
  SELECT ca.child_node_id, a.niveau + 1, a.chemin || ca.qname_localname::text
  FROM arbre a JOIN alf_child_assoc ca ON ca.parent_node_id = a.id AND ca.is_primary
  WHERE a.niveau < 12
), cibles AS (
  SELECT n.id, a.chemin FROM arbre a JOIN alf_node n ON n.id = a.id
  JOIN alf_qname qt ON qt.id = n.type_qname_id
  JOIN alf_namespace nt ON nt.id = qt.ns_id
  WHERE a.niveau > 0
    AND ((nt.uri = 'http://www.alfresco.org/model/content/1.0' AND qt.local_name = 'content')
         OR (nt.uri = '{ns}' AND qt.local_name <> 'dossier'))
  UNION
  SELECT n.id, NULL::text[] FROM alf_node n JOIN alf_store s ON s.id = n.store_id
  WHERE s.protocol = 'workspace' AND s.identifier = 'SpacesStore' AND n.uuid = ANY('{{{suivis}}}'::text[])
)
SELECT n.uuid, array_to_string(c.chemin, '/'), n.audit_created, n.audit_creator, n.audit_modified, n.audit_modifier,
       COALESCE((SELECT json_object_agg(q.local_name, p.string_value)
                 FROM alf_node_properties p JOIN alf_qname q ON q.id = p.qname_id
                 JOIN alf_namespace ns ON ns.id = q.ns_id
                 WHERE p.node_id = n.id AND ns.uri = '{ns}' AND p.string_value IS NOT NULL
                   AND q.local_name IN ({props}))::text, '{{}}'),
       COALESCE((SELECT p.string_value FROM alf_node_properties p JOIN alf_qname q ON q.id = p.qname_id
                 JOIN alf_namespace ns ON ns.id = q.ns_id
                 WHERE p.node_id = n.id AND q.local_name = 'name'
                   AND ns.uri = 'http://www.alfresco.org/model/content/1.0' LIMIT 1), '')
FROM (SELECT DISTINCT ON (id) id, chemin FROM cibles ORDER BY id, chemin NULLS LAST) c
JOIN alf_node n ON n.id = c.id"""

SQL_RACINE = """SELECT n.uuid FROM alf_node n
JOIN alf_child_assoc ca ON ca.child_node_id = n.id AND ca.is_primary AND ca.qname_localname = '{nom}'
JOIN alf_child_assoc dl ON dl.child_node_id = ca.parent_node_id AND dl.is_primary AND dl.qname_localname = 'documentLibrary'
JOIN alf_child_assoc st ON st.child_node_id = dl.parent_node_id AND st.is_primary AND st.qname_localname = '{site}'
LIMIT 1"""

SQL_PERSONNES = """SELECT
  MAX(CASE WHEN q.local_name = 'userName' THEN p.string_value END),
  MAX(CASE WHEN q.local_name = 'firstName' THEN p.string_value END),
  MAX(CASE WHEN q.local_name = 'lastName' THEN p.string_value END)
FROM alf_node n JOIN alf_qname qt ON qt.id = n.type_qname_id AND qt.local_name = 'person'
JOIN alf_store s ON s.id = n.store_id AND s.protocol = 'workspace' AND s.identifier = 'SpacesStore'
JOIN alf_node_properties p ON p.node_id = n.id JOIN alf_qname q ON q.id = p.qname_id
WHERE q.local_name IN ('userName', 'firstName', 'lastName')
GROUP BY n.id"""


# Historique des versions (version2Store) : chaque version garde l'auteur et la date de la modification
# figée (cm:modifier / cm:modified au moment de la version). Avec l'enregistrement des métadonnées en
# version (autoVersionOnUpdateProps), chaque « Enregistrer » de Share y laisse une trace datée et signée.
SQL_VERSIONS = """SELECT split_part(p.string_value, '/', 4), n.audit_modified, n.audit_modifier,
       COALESCE((SELECT vl.string_value FROM alf_node_properties vl JOIN alf_qname qv ON qv.id = vl.qname_id
                 WHERE vl.node_id = n.id AND qv.local_name = 'versionLabel' LIMIT 1), '')
FROM alf_node n JOIN alf_store s ON s.id = n.store_id AND s.protocol = 'workspace' AND s.identifier = 'version2Store'
JOIN alf_node_properties p ON p.node_id = n.id JOIN alf_qname q ON q.id = p.qname_id AND q.local_name = 'frozenNodeRef'
WHERE split_part(p.string_value, '/', 4) = ANY('{{{ids}}}'::text[])"""


def trouver_base_alfresco(pref: str | None = None) -> tuple[str, str, str]:
    """(conteneur, utilisateur, base) de la base PostgreSQL contenant alf_node."""
    noms = [pref] if pref else [l.split()[0] for l in _docker("ps", "--format", "{{.Names}} {{.Image}}").decode().splitlines()
                                if re.search(r"postgres|postgis", l, re.I)]
    for c in noms:
        try:
            u = _docker("exec", c, "printenv", "POSTGRES_USER").decode().strip() or "postgres"
        except RuntimeError:
            u = "postgres"
        for d in _docker("exec", c, "psql", "-U", u, "-d", "postgres", "-Atc",
                         "SELECT datname FROM pg_database WHERE NOT datistemplate").decode().split():
            try:
                if _docker("exec", c, "psql", "-U", u, "-d", d, "-Atc",
                           "SELECT to_regclass('alf_node') IS NOT NULL").decode().strip() == "t":
                    return c, u, d
            except RuntimeError:
                continue
    raise RuntimeError("Aucune base Alfresco (table alf_node) trouvée dans les conteneurs PostgreSQL du serveur")


class AlfrescoSql:
    def __init__(self, c: dict):
        c = c or {}
        if c.get("conteneur") and c.get("base"):
            self.ctn, self.user, self.base = c["conteneur"], c.get("utilisateur") or "alfresco", c["base"]
        else:
            self.ctn, self.user, self.base = trouver_base_alfresco(c.get("conteneur") or None)

    def sql(self, requete: str) -> list[list[str]]:
        out = _docker("exec", "-i", "-e", "PGOPTIONS=-c default_transaction_read_only=on", self.ctn,
                      "psql", "-U", self.user, "-d", self.base, "-v", "ON_ERROR_STOP=1", "-At", "-F", "\t",
                      entree=requete.encode())
        return [l.split("\t") for l in out.decode().splitlines() if l]

    def racine(self, site: str, nom: str) -> str:
        r = self.sql(SQL_RACINE.format(site=site, nom=nom))
        if not r:
            raise RuntimeError(f"Dossier {site}/documentLibrary/{nom} introuvable dans Alfresco")
        return r[0][0]

    def noeuds(self, racine: str, suivis: list[str]) -> list[dict]:
        ok = [s for s in suivis if re.fullmatch(r"[0-9a-fA-F-]{36}", s or "")]
        q = SQL_PIECES.format(racine=racine, ns=NS_MULCV, suivis=",".join(ok),
                              props=", ".join(f"'{p}'" for p in PROPS))
        out = []
        for uuid, chemin, cree, createur, modif, modifieur, props, nom in self.sql(q):
            out.append({"id": uuid, "chemin": [_decode(x) for x in chemin.split("/")] if chemin else None,
                        "cree_le": cree, "createur": createur, "modifie_le": modif, "modifieur": modifieur,
                        "props": _json(props), "nom": _decode(nom)})
        return out

    def versions(self, ids: list[str]) -> list[dict]:
        ok = [i for i in ids if re.fullmatch(r"[0-9a-fA-F-]{36}", i or "")]
        out = []
        for k in range(0, len(ok), 5000):
            for uuid, le, qui, label in self.sql(SQL_VERSIONS.format(ids=",".join(ok[k:k + 5000]))):
                out.append({"piece": uuid, "le": le, "acteur": qui, "version": label})
        return out

    def personnes(self) -> dict[str, str]:
        out = {}
        for u, prenom, nom in self.sql(SQL_PERSONNES):
            if u:
                out[u] = " ".join(x for x in (prenom, nom) if x).strip() or u
        return out


# ---------------------------------------------------------------------------
#  Assemblage
# ---------------------------------------------------------------------------
class Collecte:
    """Résultat brut normalisé : pièces, événements, noms des utilisateurs."""

    def __init__(self, pieces: list[dict], evenements: list[dict], personnes: dict[str, str], mode: str,
                 agent: list[dict] | None = None):
        self.pieces, self.evenements, self.personnes, self.mode = pieces, evenements, personnes, mode
        self.agent = agent or []   # lignes agent_indexing : node_id, outcome, total_ms…


def assembler(noeuds: list[dict], jobs: list[dict], audit: list[dict], tz: ZoneInfo, chemin_scan: list[str],
              racine_production: str) -> tuple[list[dict], list[dict]]:
    """Fusionne Alfresco (propriétés, chemin) et le journal Scan (états, événements)."""
    par_id = {n["id"]: n for n in noeuds}
    job_id = {j["node_id"]: j for j in jobs}
    pieces = []
    for nid in sorted(set(par_id) | set(job_id)):
        n, j = par_id.get(nid, {}), job_id.get(nid, {})
        p = n.get("props", {})
        chemin = n.get("chemin")
        dec = decouper_chemin(chemin[:-1] if chemin else [], racine_production) if chemin else \
            {"zone": None, "conteneur": None, "carton": None, "dossier": None}
        applied, valide = _json(j.get("applied")), _json(j.get("approved_snapshot"))
        analyse = _json(j.get("analysis"))
        pieces.append({
            "id": nid, "nom": n.get("nom"), "sous_scan": chemin is not None,
            "zone": dec["zone"], "conteneur": dec["conteneur"], "carton": p.get("boxReference") or dec["carton"],
            "dossier": p.get("dossierId") or dec["dossier"],
            "depose_le": _dt(n.get("cree_le"), tz), "depose_par": n.get("createur"),
            "modifie_le": _dt(n.get("modifie_le"), tz), "modifie_par": n.get("modifieur"),
            "etat": j.get("status") or ("NON_SUIVI" if n else None), "tentatives": int(j.get("attempts") or 0),
            "erreur": j.get("error") or None,
            "nature_reconnue": analyse.get("code"), "anomalies": json.dumps(analyse.get("warnings") or [], ensure_ascii=False),
            "proposition": json.dumps(applied, ensure_ascii=False), "validation": json.dumps(valide, ensure_ascii=False),
            "type_document": p.get("typeDocumentCode"), "nature": p.get("nature"), "rubrique": p.get("classCode"),
            "rubrique_chemin": p.get("classPath"), "plan": p.get("planVersion"),
            "statut_qualite": p.get("qualityStatus"), "statut_classement": p.get("classificationStatus"),
            "statut_ocr": p.get("ocrStatus"), "objet": p.get("subject"),
            "dans_alfresco": bool(n), "verse": j.get("status") == "ARCHIVED" or bool(j.get("record_id")),
        })
    evts = []
    for a in audit:
        evts.append({"id": int(a["id"]), "piece": a["node_id"], "evenement": a["event"], "acteur": a.get("actor") or None,
                     "le": _dt(a["at_time"], tz), "details": a.get("details") or "{}"})
    return pieces, evts


def evenements_enregistrement(pieces: list[dict], versions: list[dict], tz: ZoneInfo, premier_id: int,
                              technique=lambda a: False) -> list[dict]:
    """Enregistrements des métadonnées (« Enregistrer » dans Share) : pas journalisés par le service Scan.
    - versions Alfresco de la pièce créées par une personne : date et auteur exacts de chaque enregistrement ;
    - à défaut, pour une pièce pas encore validée, sa dernière modification si elle est le fait d'une personne
      (dernier enregistrement seulement) ;
    - une pièce validée sans enregistrement visible est comptée à sa validation (la validation exige un
      enregistrement préalable) : cette approximation est faite dans indicateurs.preparer."""
    out, k = [], premier_id
    vus = set()
    for v in versions:
        k += 1
        cle = (v["piece"], v["le"], v["acteur"])
        if cle in vus:
            continue
        vus.add(cle)
        out.append({"id": k, "piece": v["piece"], "evenement": "METADATA_SAVED", "acteur": v["acteur"],
                    "le": _dt(v["le"], tz), "details": json.dumps({"source": "version", "version": v.get("version")})})
    derniere_version = {}
    for e in out:
        if not technique(e["acteur"] or ""):
            derniere_version[e["piece"]] = max(derniere_version.get(e["piece"], e["le"]), e["le"])
    for p in pieces:
        dv = derniere_version.get(p["id"])
        if dv is not None and p.get("modifie_le") and p["modifie_le"] <= dv + timedelta(seconds=5):
            continue  # dernière modification déjà connue par l'historique des versions
        if p.get("etat") not in ("REVIEW_REQUIRED", "ERROR", "QUEUED", "RETRY", "NON_SUIVI"):
            continue
        if p.get("modifie_le") and p.get("modifie_par"):
            k += 1
            out.append({"id": k, "piece": p["id"], "evenement": "METADATA_SAVED", "acteur": p["modifie_par"],
                        "le": p["modifie_le"], "details": json.dumps({"source": "derniere_modification"})})
    return out


def evenements_degrades(noeuds: list[dict], tz: ZoneInfo) -> list[dict]:
    """Mode sans journal : validation = qualityStatus VERIFIED, par le dernier modificateur."""
    out = []
    for i, n in enumerate(noeuds, 1):
        if (n.get("props") or {}).get("qualityStatus") == "VERIFIED":
            out.append({"id": i, "piece": n["id"], "evenement": "METADATA_APPROVED", "acteur": n.get("modifieur"),
                        "le": _dt(n.get("modifie_le"), tz), "details": "{}"})
    return out


def _technique(cfg):
    rx = re.compile(cfg["perimetre"].get("comptes_techniques") or r"^$")
    return lambda a: bool(rx.search(a or ""))


def collecter(cfg: dict, tz: ZoneInfo, maintenant: datetime | None = None) -> Collecte:
    s = cfg["source"]
    if s["type"] == "demo":
        return Demo(cfg, tz, maintenant).collecte()
    if s["type"] != "serveur":
        raise ValueError(f"source.type inconnu : {s['type']}")
    alf = AlfrescoSql(s.get("alfresco_sql"))
    per = cfg["perimetre"]
    racine = per.get("racine_scan_uuid") or alf.racine(per["site"], per["dossier_scan"])
    js = s.get("journal_scan") or {}
    if js.get("actif", True):
        jobs, audit, agent = JournalScan(js).lire()
        noeuds = alf.noeuds(racine, [j["node_id"] for j in jobs])
        pieces, evts = assembler(noeuds, jobs, audit, tz, [], per.get("racine_production", "Production"))
        mode = "journal"
        evts += evenements_enregistrement(pieces, alf.versions([p["id"] for p in pieces]), tz, len(evts) + 10_000_000,
                                          _technique(cfg))
    else:
        agent = []
        noeuds = alf.noeuds(racine, [])
        pieces, _ = assembler(noeuds, [], [], tz, [], per.get("racine_production", "Production"))
        evts = evenements_degrades(noeuds, tz)
        evts += evenements_enregistrement(pieces, alf.versions([p["id"] for p in pieces]), tz, len(evts) + 10_000_000,
                                          _technique(cfg))
        mode = "degrade"
    personnes = alf.personnes()
    personnes.update({k: v for k, v in (cfg["perimetre"].get("agents_ia") or {}).items()})
    return Collecte(pieces, evts, personnes, mode, agent)


# ---------------------------------------------------------------------------
#  Démonstration : parcours complet simulé, signalé dans le rapport
# ---------------------------------------------------------------------------
class Demo:
    INDEXEURS = {"akoua.yao": (1.0, 95), "kone.ibrahim": (1.0, 80), "nguessan.marie": (.95, 120),
                 "traore.salif": (.8, 150), "diallo.awa": (.9, 105)}  # (présence relative, secondes médianes par pièce)
    NATURES = [("ATTESTATION_DOMANIALE", "3302"), ("PLAN_PARCELLAIRE", "3302"), ("ETAT_DOMANIAL", "3302"),
               ("FICHE_DEMANDE", "3301"), ("RAPPORT_GEOMETRE", "3305"), ("PIECE_IDENTITE", "1010"),
               ("ORDRE_RECETTES", "2201"), ("inconnu", "1010")]
    RUBRIQUES = {"3302": "Domaine et actes fonciers / Dossiers ACD de la DFU", "3301": "Domaine et actes fonciers / Demandes d'attribution",
                 "3305": "Domaine et actes fonciers / Rapports de géomètre", "1010": "Dossiers de base",
                 "2201": "Finances / Ordres de recettes"}
    CHAMPS = ["mulcv:nature", "mulcv:typeDocumentCode", "mulcv:classId", "mulcv:subject", "mulcv:lotNumber",
              "mulcv:blockNumber", "mulcv:surfaceArea", "mulcv:municipality", "mulcv:landTitleNumber"]

    def __init__(self, cfg, tz, maintenant=None):
        self.tz = tz
        self.maintenant = maintenant or datetime.now(tz).replace(tzinfo=None)
        self.debut = self.maintenant.date() - timedelta(days=24)
        self.agent = []

    def collecte(self) -> Collecte:
        rng = random.Random(2026)
        pieces, evts, eid = [], [], 0
        file: list[dict] = []
        j = self.debut
        k = 0
        while j <= self.maintenant.date():
            ouvre = j.weekday() < 5
            # dépôts de la numérisation
            for _ in range(rng.randint(700, 900) if ouvre else 0):
                k += 1
                t = datetime(j.year, j.month, j.day, rng.randint(8, 16), rng.randint(0, 59), rng.randint(0, 59))
                if t > self.maintenant:
                    continue
                nat, rub = rng.choices(self.NATURES, weights=[22, 18, 12, 14, 8, 10, 6, 10])[0]
                c, cart, dos = 1 + k // 4000, 1 + k // 120, 1 + k // 6
                p = {"id": f"demo-{k:06d}", "nom": f"FON-3302-113-{cart:04d}-{dos % 40:03d}-P{1 + k % 6:02d}.pdf",
                     "sous_scan": True, "zone": "Zone_Sud", "conteneur": f"CNT-{c:04d}", "carton": f"FON-3302-113-{cart:04d}",
                     "dossier": f"FON-3302-113-{cart:04d}-{dos % 40:03d}", "depose_le": t,
                     "depose_par": f"service-account-mulcv-depot-poste{1 + k % 2}", "modifie_le": t, "modifie_par": COMPTE_WORKER,
                     "etat": "QUEUED", "tentatives": 0, "erreur": None, "nature_reconnue": nat,
                     "anomalies": "[]", "proposition": "{}", "validation": "{}", "type_document": None, "nature": None,
                     "rubrique": None, "rubrique_chemin": None, "plan": "V3-2026-10-28", "statut_qualite": None,
                     "statut_classement": None, "statut_ocr": None, "objet": None, "dans_alfresco": True, "verse": False,
                     "_rub": rub}
                pieces.append(p)
                ta = t + timedelta(seconds=rng.randint(40, 600))
                if ta <= self.maintenant:
                    eid += 1
                    if rng.random() < .006:
                        p.update(etat="ERROR", erreur="IllegalStateException", tentatives=3)
                        evts.append({"id": eid, "piece": p["id"], "evenement": "ANALYSIS_FAILED", "acteur": "worker", "le": ta,
                                     "details": "{}"})
                        continue
                    warn = rng.sample(["CONFLICT_mulcv:lotNumber", "SURFACE_CONFLICT", "DESTINATION_REQUIRES_REVIEW",
                                       "MUNICIPALITY_DIFFERS_FROM_COTE"], k=rng.choices([0, 1, 2], [70, 24, 6])[0])
                    prop = {c: f"val-{rng.randint(1, 99)}" for c in self.CHAMPS if rng.random() < .7}
                    p.update(etat="REVIEW_REQUIRED", statut_ocr="COMPLETED_WITH_REVIEW", statut_qualite="REVIEW_REQUIRED",
                             statut_classement="REVIEW_REQUIRED", anomalies=json.dumps(warn), proposition=json.dumps(prop),
                             _analyse=ta)
                    evts.append({"id": eid, "piece": p["id"], "evenement": "ANALYSIS_COMPLETED", "acteur": COMPTE_WORKER,
                                 "le": ta, "details": json.dumps({"nature": nat, "warnings": warn})})
                    file.append(p)
            # indexation par les archivistes : chaque indexeur enchaîne les pièces de la file commune
            if ouvre:
                instants = []
                for u, (presence, med) in self.INDEXEURS.items():
                    if rng.random() > .93 * min(1, presence):
                        continue
                    t = datetime(j.year, j.month, j.day, 8, rng.randint(15, 50))
                    fin = datetime(j.year, j.month, j.day, 17, rng.randint(0, 30))
                    pause = datetime(j.year, j.month, j.day, 12, rng.randint(0, 30))
                    while True:
                        t += timedelta(seconds=max(25, rng.lognormvariate(0, .45) * med))
                        if pause <= t < pause + timedelta(minutes=60):
                            t = pause + timedelta(minutes=rng.randint(55, 75))
                        if rng.random() < .01:
                            t += timedelta(minutes=rng.randint(25, 50))  # réunion, coupure
                        if t >= fin or t > self.maintenant:
                            break
                        instants.append((t, u))
                # agent IA : traite des pièces la nuit et en continu, ~40 s par pièce
                ta = datetime(j.year, j.month, j.day, 7, 0)
                for _ in range(rng.randint(60, 120)):
                    ta += timedelta(seconds=rng.randint(30, 70))
                    instants.append((ta, "service-account-ceiba-ai-agent"))
                for t, u in sorted(instants):
                    dispo = [x for x in file if x["_analyse"] < t]
                    if not dispo:
                        continue
                    p = dispo[0]
                    file.remove(p)
                    val = dict(json.loads(p["proposition"]))
                    for c in rng.sample(self.CHAMPS, k=rng.choices([0, 1, 2, 3], [48, 30, 15, 7])[0]):
                        val[c] = f"corr-{rng.randint(1, 99)}"
                    val.update({"mulcv:qualityStatus": "VERIFIED", "mulcv:classificationStatus": "APPROVED"})
                    if rng.random() < .03:
                        eid += 1
                        evts.append({"id": eid, "piece": p["id"], "evenement": "ANALYSIS_REQUESTED", "acteur": u,
                                     "le": t - timedelta(seconds=20), "details": "{}"})
                    if u.startswith("service-account-"):
                        eid += 1
                        evts.append({"id": eid, "piece": p["id"], "evenement": "AGENT_INDEXED", "acteur": u, "le": t,
                                     "details": "{}"})
                        p.update(modifie_par=u, modifie_le=t)
                        self.agent.append({"node_id": p["id"], "outcome": "FILLED", "total_ms": str(rng.randint(25000, 60000)),
                                           "indexed_at": t.isoformat() + "Z", "model": "qwen2.5:7b",
                                           "fields": json.dumps({"mulcv:nature": "x", "mulcv:lotNumber": "1", "mulcv:subject": "y"})})
                        continue
                    eid += 1
                    evts.append({"id": eid, "piece": p["id"], "evenement": "METADATA_SAVED", "acteur": u,
                                 "le": t - timedelta(seconds=rng.randint(4, 12)), "details": json.dumps({"source": "version"})})
                    eid += 1
                    evts.append({"id": eid, "piece": p["id"], "evenement": "METADATA_APPROVED", "acteur": u, "le": t,
                                 "details": json.dumps({"category": p["_rub"]})})
                    p.update(etat="APPROVED", validation=json.dumps(val), rubrique=p["_rub"], statut_qualite="VERIFIED",
                             statut_classement="APPROVED", modifie_par=u, modifie_le=t,
                             type_document=p["nature_reconnue"].upper() if p["nature_reconnue"] != "inconnu" else "LETTRE",
                             rubrique_chemin=self.RUBRIQUES.get(p["_rub"], p["_rub"]))
                    # versement : aussitôt (enchaînement) ou en fin de journée
                    tv = t + timedelta(seconds=rng.randint(8, 40)) if rng.random() < .8 else \
                        datetime(j.year, j.month, j.day, 17, 25) + timedelta(seconds=rng.randint(0, 600))
                    if tv <= self.maintenant and (rng.random() < .995):
                        eid += 1
                        evts.append({"id": eid, "piece": p["id"], "evenement": "RECORD_FILED", "acteur": u, "le": tv,
                                     "details": "{}"})
                        p.update(etat="ARCHIVED", verse=True, sous_scan=False)
                # saisies enregistrées mais non validées en fin de journée
                for u in self.INDEXEURS:
                    for x in file[:rng.randint(0, 1)]:
                        if x["_analyse"] < self.maintenant:
                            x.update(modifie_par=u, modifie_le=min(self.maintenant, datetime(j.year, j.month, j.day, 17, 20)))
                            eid += 1
                            evts.append({"id": eid, "piece": x["id"], "evenement": "METADATA_SAVED", "acteur": u,
                                         "le": x["modifie_le"], "details": json.dumps({"source": "version"})})
            j += timedelta(days=1)
        for p in pieces:
            p.pop("_rub", None)
            p.pop("_analyse", None)
        noms = {"akoua.yao": "Akoua YAO", "kone.ibrahim": "Ibrahim KONÉ", "nguessan.marie": "Marie N'GUESSAN",
                "traore.salif": "Salif TRAORÉ", "diallo.awa": "Awa DIALLO",
                "service-account-ceiba-ai-agent": "Agent IA CEIBA"}
        return Collecte(pieces, evts, noms, "demo", self.agent)
