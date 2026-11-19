#!/usr/bin/env bash
# Diagnostic des envois automatiques :  sudo bash outils/diagnostic.sh
D=/opt/statisticindexation
echo "=== Minuteurs (prochain / dernier passage)"
systemctl list-timers 'indexation-*' --all --no-pager
echo; echo "=== État des derniers lancements"
for t in midi jour hebdo; do
  systemctl show "indexation-rapport@$t.service" -p ActiveState -p Result -p ExecMainStatus -p ExecMainExitTimestamp --no-pager | tr '\n' ' '; echo " ($t)"
done
echo; echo "=== Journal systemd (24 h)"
journalctl -u 'indexation-rapport@*' --since "24 hours ago" --no-pager | tail -40
echo; echo "=== Journaux du rapport"
for f in "$D"/journal/*.log; do [ -f "$f" ] && { echo "--- $f"; tail -25 "$f"; }; done
echo; echo "=== Clé Resend présente ?"
grep -q '^RESEND_API_KEY=re_' "$D/.env" && echo "oui" || echo "NON : renseigner RESEND_API_KEY dans $D/.env"
echo; echo "=== Envois enregistrés"
"$D/.venv/bin/python" - <<'PY'
import sqlite3
try:
    for r in sqlite3.connect("/opt/statisticindexation/data/indexation.db").execute("select cle, envoye_le, sujet from envoi order by envoye_le desc limit 10"):
        print(*r, sep="  |  ")
except Exception as e:
    print("aucun envoi enregistré :", e)
PY
