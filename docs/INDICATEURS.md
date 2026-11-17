# Définitions des indicateurs

## Grains

| Grain | Définition | Source |
|---|---|---|
| Pièce | Document (`cm:content` ou type `mulcv:*` autre que `mulcv:dossier`) sous le dossier Scan, ou suivi par le service Scan | Alfresco, `scan_job` |
| Pièce indexée | Pièce dont les métadonnées ont été **validées** par une personne (`METADATA_APPROVED`). Comptée une fois, à sa **première** validation | `scan_audit` |
| Reprise | Validation d'une pièce déjà indexée (après relance d'analyse ou correction) | `scan_audit` |
| Indexeur | Utilisateur Alfresco auteur de la validation. Nom lu dans son profil (`cm:firstName`, `cm:lastName`). Les comptes techniques (`perimetre.comptes_techniques`) sont exclus | `scan_audit`, Alfresco |
| Pièce versée | Record déclaré et classé dans le plan RM (`RECORD_FILED`) | `scan_audit` |
| Stock à indexer | Pièce analysée (ou déposée) et pas encore validée, hors analyse en échec | reconstitué à toute date depuis les événements |
| Dossier entièrement indexé | Dossier dont toutes les pièces connues sont indexées ; daté par sa dernière pièce | |

## Temps

**Temps d'indexation d'une pièce.** Le formulaire Share enchaîne les pièces (« la pièce suivante à indexer s'ouvre au bout de 5 s »). Le temps d'une pièce est donc l'écart entre sa validation et la validation précédente **du même indexeur, le même jour**, lorsqu'il est compris entre `mesure.temps_min_s` (5 s) et `mesure.session_max_min` (20 min). Il inclut la lecture du PDF, la saisie, l'enregistrement, la validation et, le cas échéant, le versement.

- Écart supérieur à 20 min : début d'une nouvelle session (pause, réunion) ; la première pièce d'une session n'a pas de temps mesuré.
- Écart inférieur à 5 s : validation en lot, non retenue (signalée dans le rapport).
- On publie la **médiane** et le **90ᵉ centile**, peu sensibles aux valeurs extrêmes, plutôt que la moyenne.

**Heures d'indexation** d'un indexeur : somme de ses temps mesurés + son temps médian pour chaque pièce non mesurée. **Cadence** = validations / heures d'indexation.

**Attente avant indexation** : de la fin de l'analyse automatique (à défaut, du dépôt) à la première validation. **Délai de versement** : de la première validation au versement.

## Qualité

- **Champs corrigés** : champs `mulcv:*` dont la valeur validée (`scan_job.approved_snapshot`) diffère de la proposition de l'analyse (`scan_job.applied`). Les propriétés posées par le serveur (`sha256`, statuts, `classCode`, `classPath`, `planVersion`…) ne comptent pas.
- **Acceptées sans correction** : part des pièces indexées sans aucun champ corrigé — mesure directe de l'utilité de l'analyse automatique.
- **Reconnaissance automatique** : part des analyses dont la nature proposée n'est pas « inconnu ».
- **Anomalies** : avertissements de l'analyse (`SURFACE_CONFLICT`, `DESTINATION_REQUIRES_REVIEW`, `MUNICIPALITY_DIFFERS_FROM_COTE`, `CONFLICT_<champ>`, `DOSSIER_ID_CONFLICT`).
- **Saisies non validées** : pièces encore « à vérifier » dont la dernière modification Alfresco est celle d'une personne (enregistrées, pas encore validées).

## Comparaisons

- Point du jour : moyenne des 10 derniers jours ouvrés **avec indexation**, à la même heure.
- Hebdomadaire : même période de la semaine précédente.
- Une variation n'est commentée que si elle est significative : a + b ≥ 20 et |a − b| / √(a + b) ≥ 2.
- Résorption du stock : stock / (indexées par jour − nouvelles par jour), moyennes des 10 derniers jours d'indexation ; non calculée si le stock augmente.

## Limites

- Le service Scan ne journalise pas l'ouverture d'une pièce ni les enregistrements intermédiaires : le temps est mesuré entre validations successives. Une pièce enregistrée un jour et validée le lendemain n'a pas de temps mesuré.
- Les documents indexés hors du parcours Scan (formulaires du portail, autres sites) ne sont pas suivis par ce rapport.
- Le nombre de pages n'est pas enregistré dans le SAE ; il n'est pas utilisé ici.
