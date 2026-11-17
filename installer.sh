#!/usr/bin/env bash
# Installation du service de suivi de l'indexation (sans interface web).
#   sudo bash installer.sh            installe / met à jour
# Le dossier doit se trouver dans /opt/statisticindexation.
set -euo pipefail

DOSSIER=/opt/statisticindexation
UTILISATEUR=indexation

[ "$(id -u)" -eq 0 ] || { echo "❌ Lancer avec sudo : sudo bash installer.sh"; exit 1; }
[ "$(cd "$(dirname "$0")" && pwd)" = "$DOSSIER" ] || { echo "❌ Ce dossier doit être $DOSSIER (git clone ... $DOSSIER)"; exit 1; }

echo "▶ Paquets système"
if python3 -c "import venv, ensurepip" 2>/dev/null; then
  echo "  python3 + venv déjà présents : apt non nécessaire"
else
  if ! getent hosts archive.ubuntu.com >/dev/null; then
    echo "❌ Le serveur ne résout pas archive.ubuntu.com (DNS). Corriger puis relancer :"
    echo "     resolvectl dns eth0 1.1.1.1 8.8.8.8     # ou éditer /etc/resolv.conf"
    echo "     getent hosts archive.ubuntu.com         # doit afficher une adresse"
    exit 1
  fi
  apt-get update -qq || true
  apt-get install -y -qq python3 python3-venv python3-pip >/dev/null
fi
apt-get install -y -qq fonts-dejavu-core >/dev/null 2>&1 || echo "  (police DejaVu non installée : graphiques avec la police par défaut)"

echo "▶ Accès Docker (base Alfresco, journal du service Scan)"
command -v docker >/dev/null || { echo "❌ docker absent : ce service s'installe sur le serveur du SAE"; exit 1; }
docker ps --format '{{.Names}} {{.Image}}' | grep -Ei 'postgres' >/dev/null || echo "  ⚠ aucun conteneur PostgreSQL visible"
docker ps --format '{{.Names}} {{.Image}}' | grep -Ei 'mulcv-scan' >/dev/null || echo "  ⚠ conteneur du service Scan introuvable : mode dégradé possible (voir README)"

echo "▶ Compte de service « $UTILISATEUR »"
id "$UTILISATEUR" >/dev/null 2>&1 || useradd --system --home "$DOSSIER" --shell /usr/sbin/nologin "$UTILISATEUR"

echo "▶ Environnement Python"
[ -d "$DOSSIER/.venv" ] || python3 -m venv "$DOSSIER/.venv"
"$DOSSIER/.venv/bin/pip" install -q --upgrade pip || { echo "❌ pip ne joint pas pypi.org (réseau / DNS)"; exit 1; }
"$DOSSIER/.venv/bin/pip" install -q -r "$DOSSIER/requirements.txt"

echo "▶ Dossiers et droits"
mkdir -p "$DOSSIER/data" "$DOSSIER/out" "$DOSSIER/journal"
if [ ! -f "$DOSSIER/.env" ]; then
  cp "$DOSSIER/.env.example" "$DOSSIER/.env"
  echo "  ⚠ .env créé depuis .env.example : renseigner RESEND_API_KEY."
fi
chown -R "$UTILISATEUR:$UTILISATEUR" "$DOSSIER/data" "$DOSSIER/out" "$DOSSIER/journal"
chown root:"$UTILISATEUR" "$DOSSIER/.env" && chmod 640 "$DOSSIER/.env"

echo "▶ Planification systemd (fuseau Africa/Abidjan)"
cp "$DOSSIER"/deploiement/systemd/* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now indexation-jour.timer indexation-hebdo.timer

echo
echo "✅ Installé. Prochains envois :"
systemctl list-timers 'indexation-*' --no-pager | head -5
echo
echo "Tester sans rien envoyer :   $DOSSIER/.venv/bin/python $DOSSIER/rapport_indexation.py jour --apercu"
echo "Envoyer un test à une adresse : $DOSSIER/.venv/bin/python $DOSSIER/rapport_indexation.py jour --a vous@ceiba-analytics.com"
echo "Journal :                    tail -f $DOSSIER/journal/*.log"
