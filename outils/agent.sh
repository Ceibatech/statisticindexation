#!/usr/bin/env bash
# Diagnostic de l'agent d'indexation IA (ceiba-ai-agent) :  sudo bash outils/agent.sh
# Lecture seule : n'écrit rien, n'affiche pas le secret de l'agent.
D=/opt/statisticindexation
SCAN=$(docker ps --format '{{.Names}} {{.Image}}' | grep -i 'mulcv-scan' | awk '{print $1}' | head -1)
echo "=== Conteneur du service Scan : ${SCAN:-INTROUVABLE}"
DATA=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$SCAN" 2>/dev/null)
echo; echo "=== Réglages de l'agent ($DATA/agent.properties, secret masqué)"
[ -f "$DATA/agent.properties" ] && sed 's/^client-secret=.*/client-secret=***/' "$DATA/agent.properties" || echo "  ABSENT : l'agent est désactivé"
echo; echo "=== Résultats de l'agent (table agent_indexing)"
"$D/.venv/bin/python" - <<'PY'
import sys, yaml
from collections import Counter
sys.path.insert(0, "/opt/statisticindexation")
from indexation import sources
cfg = yaml.safe_load(open("/opt/statisticindexation/config.yaml"))
_, _, agent = sources.JournalScan(cfg["source"].get("journal_scan") or {}).lire()
print("  total :", len(agent))
for k, n in Counter(a.get("outcome") for a in agent).most_common():
    print(f"  {n:7}  {k}")
print("  par jour (FILLED / total) :")
jours = Counter((a.get("indexed_at") or "")[:10] for a in agent)
pleins = Counter((a.get("indexed_at") or "")[:10] for a in agent if a.get("outcome") == "FILLED")
for j in sorted(jours)[-5:]:
    print(f"    {j}  {pleins[j]:6} / {jours[j]}")
print("  erreurs :", Counter((a.get("error") or "")[:70] for a in agent if a.get("error")).most_common(4))
print("  10 dernières pièces examinées :")
for a in sorted(agent, key=lambda x: x.get("indexed_at") or "")[-10:]:
    print(f"    {a.get('indexed_at','')[:19]}  {a.get('outcome'):14} ocr {a.get('ocr_ms') or 0:>6} ms  {a.get('node_id')}")
# Dernier modificateur Alfresco des pièces « à vérifier » : l'agent ne prend que celles de l'analyse du scan
jobs, _, _ = sources.JournalScan(cfg["source"].get("journal_scan") or {}).lire()
attente = [j["node_id"] for j in jobs if j.get("status") == "REVIEW_REQUIRED"]
alf = sources.AlfrescoSql(cfg["source"].get("alfresco_sql"))
noeuds = {n["id"]: n for n in alf.noeuds(cfg["perimetre"]["racine_scan_uuid"], attente)}
print(f"\n=== Dernier modificateur des {len(attente)} pièces à vérifier (Alfresco)")
for k, n in Counter(noeuds[i]["modifieur"] if i in noeuds else "(absente d'Alfresco)" for i in attente).most_common(8):
    print(f"  {n:7}  {k}")
der = [noeuds[a["node_id"]] for a in sorted(agent, key=lambda x: x.get("indexed_at") or "")[-5:] if a["node_id"] in noeuds]
print("  modificateur des 5 dernières pièces sautées :", [(d["modifieur"], d["modifie_le"][:19]) for d in der])
PY
echo; echo "=== Journal du service Scan (agent, 24 h)"
docker logs "$SCAN" --since 24h 2>&1 | grep -E 'AGENT_' | cut -c1-200 | tail -15
echo "  nombre de pauses « pas le droit » sur 24 h : $(docker logs "$SCAN" --since 24h 2>&1 | grep -c AGENT_PAUSED)"
echo; echo "=== Modèle (Ollama)"
OLL=$(docker ps --format '{{.Names}}' | grep -i ollama | head -1)
if [ -n "$OLL" ]; then echo "  conteneur : $OLL"; docker exec "$OLL" ollama list 2>&1 | head -5; else echo "  AUCUN conteneur Ollama démarré : l'agent ne peut pas lire les pièces"; fi
