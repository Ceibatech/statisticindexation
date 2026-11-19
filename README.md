# statisticindexation

Service serveur (sans interface) qui suit **l'indexation des archives dans le SAE MULCV** et envoie par e-mail un tableau de bord : combien de pièces sont indexées, **par qui**, **en combien de temps**, ce qui reste à indexer et la qualité des propositions automatiques.

Il complète [`numerisation-stats`](https://github.com/Ceibatech/numerisation-stats) (production des postes de scan) : la numérisation dépose les pièces dans `Scan/Production/…`, l'indexation les qualifie dans Share, puis les verse aux archives.

| Envoi | Quand | Contenu |
|---|---|---|
| Point de mi-journée | lun.–ven. **12h30** | Indexation de la matinée, comparée à la moyenne des 10 derniers jours d'indexation à la même heure |
| Point du jour | lun.–ven. **18h00** | Indexation de la journée, même comparaison |
| Récapitulatif | vendredi **18h20** | Semaine vs semaine précédente, tendance sur 20 jours ouvrés |

Expéditeur : `indexation@ceiba-analytics.com` (domaine vérifié dans Resend). Destinataires : md, dac, dac01, dac03, daf, dao, dao01 @ceiba-analytics.com. « Répondre à tous » écrit à toute la liste. Pas d'envoi les week-ends ni les jours fériés de `config.yaml`. Un classeur Excel détaillé est joint à chaque envoi.

## Le parcours suivi

D'après `Ceibatech/MULCV-alfresco` (branche `Dev`, `services/scan` et `WORKFLOW.md`) :

```
dépôt du PDF par un poste      cm:created (service-account-mulcv-depot-posteN)
  → analyse automatique         ANALYSIS_COMPLETED   (OCR Tesseract, proposition de type, rubrique, champs)
  → À vérifier par l'archiviste  scan_job.status = REVIEW_REQUIRED
  → INDEXATION                   l'indexeur renseigne les 4 étapes du formulaire Share et clique « Enregistrer »
                                 (version Alfresco / cm:modifier, cm:modified)
  → validation                   METADATA_APPROVED    (archiviste ; contrôle droits, complétude, SHA-256)
  → versement aux archives       RECORD_FILED         (record déclaré et classé dans le plan RM V3)
```

## Contenu du rapport

- **Tuiles** : pièces indexées (vs objectif et référence), temps médian par pièce (et 90ᵉ centile), indexeurs actifs avec leurs heures d'indexation et leur cadence, stock à indexer et date de la plus ancienne pièce en attente.
- **Indicateurs complémentaires** : pièces versées et délai de versement, attente avant indexation, part des pièces acceptées sans correction, taux de reconnaissance automatique.
- **Lecture de l'analyste** : constats classés Critique / Attention / Conforme / Info (objectif, stock qui augmente, pièces anciennes, indexeur nettement plus lent que l'équipe, taux de correction, validations en lot, analyses en échec, pièces validées non versées…).
- **Par indexeur** : pièces validées, part, temps médian et 90ᵉ centile, pièces par heure, champs corrigés par pièce, horaires, saisies enregistrées mais non validées.
- **Humains vs agent IA** : pièces indexées, temps par pièce, pièces par heure, champs remplis, exactitude à la validation de l'archiviste ; résultats de l'agent (remplies, rien trouvé, laissées à l'humain, refus de droit, erreurs).
- **Graphiques** : indexations heure par heure (ou par jour pour l'hebdo), répartition des temps d'indexation, tendance sur 20 jours ouvrés, stock à indexer en fin de journée.
- **Parcours des pièces** (état courant), ancienneté du stock, date de résorption au rythme actuel.
- **Qualité** : champs les plus corrigés par les indexeurs, anomalies relevées par l'analyse, relances, échecs.
- **Rubriques de classement** des pièces indexées, vue cumulée depuis le début.

Les définitions précises sont dans [docs/INDICATEURS.md](docs/INDICATEURS.md).

## Accès au SAE — lecture seule, sans compte API

**Une pièce est indexée quand ses métadonnées sont enregistrées** par une personne ; la validation de l'archiviste et le versement sont suivis à part. L'enregistrement est daté par l'historique des versions Alfresco, à défaut par la dernière modification, à défaut par la validation (voir [docs/INDICATEURS.md](docs/INDICATEURS.md#datation-des-enregistrements)) ; le rapport indique la part de chaque datation.

Le service tourne sur le serveur du SAE et lit deux sources par `docker` :

1. **Le journal du service Scan** (base H2 `/data/scan.mv.db` du conteneur `mulcv-scan`) : états, événements horodatés et signés, propositions et valeurs validées. La base étant utilisée par le service, on en copie le fichier (`docker cp`) et on lit la **copie** avec le pilote H2 embarqué dans l'image du service (même version que le fichier), dans un conteneur jetable **sans réseau**. Le service Scan n'est ni arrêté ni modifié. Le mot de passe est lu dans la configuration du conteneur.
2. **La base PostgreSQL d'Alfresco** (comme `numerisation-stats`) en transaction `default_transaction_read_only=on` : propriétés `mulcv:*`, dates et auteurs, arborescence `Scan/Production/<zone>/<conteneur>/<carton>/<dossier>`, noms des utilisateurs. Les pièces versées, sorties du dossier Scan, sont retrouvées par leur identifiant.

Conteneurs, base et racine Scan sont détectés automatiquement ; on peut les fixer dans `config.yaml` (`source.alfresco_sql`, `source.journal_scan`, `perimetre.racine_scan_uuid`).

**Mode dégradé** (`source.journal_scan.actif: false`, ou si le service Scan n'est pas sur ce serveur) : seule la base Alfresco est lue ; une pièce est indexée si `mulcv:qualityStatus = VERIFIED`, par son dernier modificateur, à sa dernière modification. Le rapport le signale ; les temps y sont approximatifs.

## Installation sur le serveur

```bash
sudo git clone https://github.com/Ceibatech/statisticindexation.git /opt/statisticindexation
cd /opt/statisticindexation
sudo bash installer.sh
sudo nano .env            # RESEND_API_KEY
sudo nano config.yaml     # objectifs, jours fériés
```

L'installation crée l'environnement Python et trois minuteurs systemd (`indexation-midi`, `indexation-jour`, `indexation-hebdo`) au fuseau **Africa/Abidjan**.

```bash
# Aperçu sans envoi (écrit out/jour-AAAA-MM-JJ.html, les images et le classeur Excel)
sudo .venv/bin/python rapport_indexation.py jour --apercu
# Envoi de test à une seule adresse
sudo .venv/bin/python rapport_indexation.py jour --a dao@ceiba-analytics.com
# Qui a touché les pièces un jour donné (Alfresco, versions, journal Scan, agent IA)
sudo .venv/bin/python outils/qui.py 2026-11-18
# Diagnostic des envois automatiques
sudo bash outils/diagnostic.sh
# Contrôle des chiffres par jour et par indexeur
sudo .venv/bin/python outils/controle.py 2026-11-17
# Démonstration sans accès au SAE
.venv/bin/python rapport_indexation.py jour --demo --apercu
# Prochains envois / journal
systemctl list-timers 'indexation-*'
tail -f journal/*.log
```

Mise à jour : `cd /opt/statisticindexation && sudo git pull && sudo bash installer.sh`.

## Garde-fous

- **Pas de rapport faux.** Si la lecture échoue ou ne trouve aucune pièce, le rapport n'est pas diffusé ; une alerte technique part seule vers `email.alerte_technique`.
- **Pas de doublon.** Chaque créneau n'est envoyé qu'une fois par jour (journal SQLite et `Idempotency-Key` Resend).
- **Rien n'est écrit dans le SAE.** Transaction PostgreSQL en lecture seule ; journal H2 lu sur une copie, dans un conteneur sans réseau.
- **Démonstration et mode dégradé signalés** en tête du rapport.

## Tests

```bash
python -m pytest tests -q
# avec le pilote H2 et une base PostgreSQL de test chargée avec tests/alfresco_mini.sql :
H2_JAR=/chemin/h2-2.3.232.jar PG_TEST="-h /tmp/pg -p 5499 -U alfresco -d alfresco" python -m pytest tests -q
```

Les tests couvrent les règles (temps par enchaînement, validations en lot, reprises, comptes techniques, corrections, stock et ancienneté), l'export réel du journal H2 au schéma du service Scan (`tests/schema_scan.sql`) et les requêtes Alfresco sur un sous-ensemble du schéma.

## Arborescence

```
rapport_indexation.py        point d'entrée (jour | hebdo)
config.yaml                  périmètre, objectifs, mesure du temps, calendrier, diffusion
indexation/sources.py        lecture : journal du service Scan (H2), base Alfresco, démonstration
indexation/indicateurs.py    indicateurs et lecture de l'analyste
indexation/graphiques.py     graphiques PNG intégrés au mail
indexation/rendu.py          mise en forme ; templates/rapport.html.j2
indexation/exports.py        classeur Excel joint
indexation/envoi.py          envoi Resend
outils/controle.py           contrôle manuel des chiffres
installer.sh, deploiement/   installation, minuteurs systemd
```

Conception et développement : Idriss Bado Olivier — CEIBA Analytics.
