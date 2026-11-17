"""Tests des règles de calcul et de la lecture du journal H2 du service Scan.
    python -m pytest tests -q
Le test H2 s'exécute si java et un pilote h2-*.jar (variable H2_JAR) sont disponibles."""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, time
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from indexation import indicateurs as I  # noqa: E402
from indexation import sources  # noqa: E402

CFG = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml", encoding="utf-8"))
CAL = I.Calendrier([0, 1, 2, 3, 4], set(), time(8), time(17, 30))


def piece(i, **kw):
    p = {"id": i, "nom": f"{i}.pdf", "sous_scan": True, "zone": "Zone_Sud", "conteneur": "CNT-1", "carton": "C1",
         "dossier": "D1", "depose_le": datetime(2026, 11, 17, 8, 0), "depose_par": "service-account-mulcv-depot-poste1",
         "modifie_le": None, "modifie_par": None, "etat": "REVIEW_REQUIRED", "tentatives": 1, "erreur": None,
         "nature_reconnue": "etat_domanial", "anomalies": "[]", "proposition": "{}", "validation": "{}",
         "type_document": None, "nature": None, "rubrique": None, "rubrique_chemin": None, "plan": None,
         "statut_qualite": None, "statut_classement": None, "statut_ocr": None, "objet": None, "dans_alfresco": True,
         "verse": False}
    p.update(kw)
    return p


def evt(k, piece_, e, acteur, h, m, s=0):
    return {"id": k, "piece": piece_, "evenement": e, "acteur": acteur, "le": datetime(2026, 11, 17, h, m, s), "details": "{}"}


def test_decoupage_du_chemin():
    d = sources.decouper_chemin(["Production", "Zone_Sud", "CNT-1", "BOX 2", "FON-3302-113-0001-012"])
    assert d == {"zone": "Zone_Sud", "conteneur": "CNT-1", "carton": "BOX 2", "dossier": "FON-3302-113-0001-012"}
    assert sources.decouper_chemin(["BOX 2"])["dossier"] == "BOX 2"


def test_corrections_ignorent_les_proprietes_techniques():
    prop = json.dumps({"mulcv:nature": "Plan", "mulcv:lotNumber": "12", "mulcv:sha256": "a", "mulcv:qualityStatus": "REVIEW_REQUIRED"})
    val = json.dumps({"mulcv:nature": "Plan", "mulcv:lotNumber": "13", "mulcv:subject": "Lot 13", "mulcv:sha256": "a",
                      "mulcv:qualityStatus": "VERIFIED", "mulcv:classCode": "3302", "$type": "mulcv:plan"})
    assert I.corrections(prop, val) == ["mulcv:lotNumber", "mulcv:subject"]
    assert I.corrections(prop, "{}") == []


def test_temps_d_indexation_par_enchainement():
    pieces = [piece(f"p{i}", etat="APPROVED") for i in range(5)]
    evts = [evt(1, "p0", "METADATA_APPROVED", "awa", 9, 0),
            evt(2, "p1", "METADATA_APPROVED", "awa", 9, 2),          # 120 s : mesuré
            evt(3, "p2", "METADATA_APPROVED", "awa", 9, 2, 2),       # 2 s : validation en lot, non mesurée
            evt(4, "p3", "METADATA_APPROVED", "awa", 10, 0),         # 58 min : nouvelle session
            evt(5, "p4", "METADATA_APPROVED", "service-account-mulcv-scan-worker", 10, 1),  # compte technique exclu
            evt(6, "p1", "METADATA_APPROVED", "awa", 10, 3)]         # reprise d'une pièce déjà indexée
    P, E, V = I.preparer(pieces, evts, {"awa": "Awa DIALLO"}, CFG)
    assert list(V["acteur"].unique()) == ["awa"]
    assert V["temps_s"].dropna().tolist() == [120.0, 180.0]
    assert int(V["en_lot"].sum()) == 1
    c = I.periode(P, E, V, datetime(2026, 11, 17), datetime(2026, 11, 18))
    assert c["indexees"] == 4 and c["reprises"] == 1 and c["validations"] == 5
    assert P.set_index("id").loc["p1", "valide_le"] == datetime(2026, 11, 17, 9, 2)


def test_stock_et_vieillissement():
    pieces = [piece("a", depose_le=datetime(2026, 11, 10, 9)), piece("b"), piece("c", etat="ERROR"),
              piece("d", etat="APPROVED")]
    evts = [evt(1, "d", "METADATA_APPROVED", "awa", 9, 30)]
    P, E, V = I.preparer(pieces, evts, {}, CFG)
    S = I.stock_a(P, datetime(2026, 11, 17, 18))
    assert sorted(S["id"]) == ["a", "b"]
    age = I.vieillissement(S, datetime(2026, 11, 17, 18), CAL)
    assert age["<1 j"] == 1 and age["3-7 j"] == 1


def test_rapport_demo_complet():
    cfg = dict(CFG, source={"type": "demo"})
    maintenant = datetime(2026, 11, 17, 18, 5)
    col = sources.collecter(cfg, None, maintenant)
    P, E, V = I.preparer(col.pieces, col.evenements, col.personnes, cfg)
    for t in ("jour", "hebdo"):
        r = I.construire(P, E, V, cfg, CAL, maintenant, t, col.personnes, col.mode)
        assert r.cur["indexees"] > 0 and not r.indexeurs.empty and r.constats
        assert r.indexeurs["validations"].sum() == r.cur["validations"]


SCHEMA = (Path(__file__).resolve().parent / "schema_scan.sql").read_text()


@pytest.mark.skipif(not (shutil.which("java") and os.environ.get("H2_JAR")), reason="java + H2_JAR requis")
def test_export_du_journal_h2(tmp_path):
    jar = os.environ["H2_JAR"]
    data = [
        "INSERT INTO scan_job(node_id,status,attempts,updated_at,analysis,applied,approved_by,approved_snapshot) VALUES "
        "('11111111-1111-1111-1111-111111111111','ARCHIVED',1,'2026-11-17T09:05:00Z',"
        "'{\"code\":\"etat_domanial\",\"warnings\":[\"SURFACE_CONFLICT\"]}','{\"mulcv:lotNumber\":\"12\"}','awa',"
        "'{\"mulcv:lotNumber\":\"13\";\"x\":1}')",
        "INSERT INTO scan_audit(node_id,event,actor,at_time,details) VALUES "
        "('11111111-1111-1111-1111-111111111111','ANALYSIS_COMPLETED','service-account-mulcv-scan-worker','2026-11-17T09:00:00Z','{}'),"
        "('11111111-1111-1111-1111-111111111111','METADATA_APPROVED','awa','2026-11-17T09:03:10.123456Z','{\"sha256\":\"x\"}'),"
        "('11111111-1111-1111-1111-111111111111','RECORD_FILED','awa','2026-11-17T09:03:30Z','{}')",
    ]
    (tmp_path / "init.sql").write_text(SCHEMA + ";\n".join(data).replace(';"x"', ',"x"') + ";\n")
    url = f"jdbc:h2:file:{tmp_path}/scan"
    subprocess.run(["java", "-cp", jar, "org.h2.tools.RunScript", "-url", url, "-user", "sa", "-password", "pw",
                    "-script", str(tmp_path / "init.sql")], check=True, capture_output=True)
    (tmp_path / "export.sql").write_text(sources.EXPORT_SQL.replace("/w/", f"{tmp_path}/"))
    subprocess.run(["java", "-cp", jar, "org.h2.tools.RunScript", "-url", url, "-user", "sa", "-password", "pw",
                    "-script", str(tmp_path / "export.sql")], check=True, capture_output=True)
    jobs, audit = sources.lire_csv(tmp_path / "job.csv"), sources.lire_csv(tmp_path / "audit.csv")
    assert jobs[0]["status"] == "ARCHIVED" and json.loads(jobs[0]["analysis"])["warnings"] == ["SURFACE_CONFLICT"]
    assert [a["event"] for a in audit] == ["ANALYSIS_COMPLETED", "METADATA_APPROVED", "RECORD_FILED"]
    from zoneinfo import ZoneInfo
    pieces, evts = sources.assembler([], jobs, audit, ZoneInfo("Africa/Abidjan"), [], "Production")
    P, E, V = I.preparer(pieces, evts, {}, CFG)
    x = P.iloc[0]
    assert x["valide_par"] == "awa" and x["verse_le"] == datetime(2026, 11, 17, 9, 3, 30)
    assert x["champs_corriges"] == ["mulcv:lotNumber"]


@pytest.mark.skipif(not os.environ.get("PG_TEST"), reason="PG_TEST='-h hôte -p port -U utilisateur -d base' requis")
def test_requetes_alfresco_sur_schema_reduit():
    """Base de test chargée avec tests/alfresco_mini.sql (sous-ensemble du schéma Alfresco)."""
    class Base(sources.AlfrescoSql):
        def __init__(self):
            pass

        def sql(self, q):
            r = subprocess.run(["psql", *os.environ["PG_TEST"].split(), "-v", "ON_ERROR_STOP=1", "-At", "-F", "\t"],
                               input=q.encode(), capture_output=True, check=True)
            return [l.split("\t") for l in r.stdout.decode().splitlines() if l]

    b = Base()
    racine = b.racine("mulcv", "Scan")
    assert racine == "20bb0608-427e-40e7-bb06-08427eb0e773"
    n = {x["id"]: x for x in b.noeuds(racine, ["bbbbbbbb-0000-0000-0000-000000000003", "x'; DROP TABLE alf_node; --"])}
    assert set(n) == {"aaaaaaaa-0000-0000-0000-000000000001", "aaaaaaaa-0000-0000-0000-000000000002",
                      "bbbbbbbb-0000-0000-0000-000000000003"}            # vignette exclue, pièce versée retrouvée
    p1 = n["aaaaaaaa-0000-0000-0000-000000000001"]
    assert p1["chemin"][:5] == ["Production", "Zone_Sud", "CNT-0001", "BOX 2", "FON-3302-113-0001-012"]
    assert p1["props"]["classCode"] == "3302" and p1["nom"].endswith("-P01.pdf")
    assert b.personnes() == {"awa": "Awa DIALLO"}
    from zoneinfo import ZoneInfo
    pieces, _ = sources.assembler(list(n.values()), [], [], ZoneInfo("Africa/Abidjan"), [], "Production")
    evts = sources.evenements_degrades(list(n.values()), ZoneInfo("Africa/Abidjan"))
    assert {e["piece"] for e in evts} == {"aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000003"}
    x = next(p for p in pieces if p["id"].endswith("01"))
    assert (x["zone"], x["conteneur"], x["carton"]) == ("Zone_Sud", "CNT-0001", "BOX 2")
