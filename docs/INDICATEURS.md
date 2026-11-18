# Définitions des indicateurs

## Grains

| Grain | Définition | Source |
|---|---|---|
| Pièce | Document (`cm:content` ou type `mulcv:*` autre que `mulcv:dossier`) sous le dossier Scan, ou suivi par le service Scan | Alfresco, `scan_job` |
| Pièce indexée | Pièce dont les métadonnées ont été **enregistrées** par une personne (bouton « Enregistrer » du formulaire Share). Comptée une fois, à son **premier** enregistrement | voir « Datation » |
| Reprise | Nouvel enregistrement d'une pièce déjà indexée, par une autre personne ou dans une autre session | |
| Pièce validée | Contrôle de l'archiviste dans le panneau Scan (`METADATA_APPROVED`) — suivi à part | `scan_audit` |
| Indexeur | Utilisateur Alfresco auteur de l'enregistrement. Nom lu dans son profil (`cm:firstName`, `cm:lastName`). Les comptes techniques (`perimetre.comptes_techniques`) sont exclus | Alfresco |
| Pièce versée | Record déclaré et classé dans le plan RM (`RECORD_FILED`) | `scan_audit` |
| Stock à indexer | Pièce analysée (ou déposée) dont les métadonnées n'ont pas encore été enregistrées, hors analyse en échec | reconstitué à toute date |
| Dossier entièrement indexé | Dossier dont toutes les pièces connues sont indexées ; daté par sa dernière pièce | |

## Agent IA d'indexation

L'agent `ceiba-ai-agent` (service Scan, branche `Dev` de MULCV-alfresco) enregistre les métadonnées sous le compte `service-account-ceiba-ai-agent`. Il est **compté comme un indexeur** (`perimetre.agents_ia`), avec ses propres lignes dans le rapport :

- ses indexations viennent du journal du service Scan (`AGENT_INDEXED`), datées exactement ;
- son temps par pièce est son temps de traitement réel (OCR + modèle, `agent_indexing.total_ms`) ;
- il est exclu du temps médian, des heures et de la cadence de l'équipe, pour ne pas fausser la mesure du travail humain ;
- une pièce qu'il a remplie puis qu'une personne reprend compte comme indexée par l'agent ; le travail de la personne est une reprise, comptée dans son activité et son temps.

## Datation des enregistrements

L'enregistrement des métadonnées est l'enregistrement natif de Share : le service Scan ne le journalise pas. Chaque indexation est datée, dans cet ordre de préférence :

1. **Historique des versions Alfresco** (`version2Store`) : chaque version garde l'auteur et la date de la modification. Exact lorsque l'enregistrement des propriétés crée une version (`autoVersionOnUpdateProps`).
2. **Dernière modification** de la pièce (`cm:modifier`, `cm:modified`) lorsqu'elle n'est pas encore validée : exacte pour le dernier enregistrement.
3. **Validation** : une pièce validée sans enregistrement daté est comptée à sa validation, au nom du validateur (la validation exige un enregistrement préalable).

Le rapport indique la part de chaque datation ; au-delà de 50 % de 2 et 3, il signale que les temps sont approximatifs.

## Temps

**Temps d'indexation d'une pièce.** Le formulaire Share enchaîne les pièces (« la pièce suivante à indexer s'ouvre au bout de 5 s »). Le temps d'une pièce est donc l'écart entre son dernier enregistrement et celui de la pièce indexée juste avant **par la même personne, le même jour**, lorsqu'il est compris entre `mesure.temps_min_s` (5 s) et `mesure.session_max_min` (20 min). Plusieurs enregistrements successifs d'une même pièce (corrections) forment une seule indexation.

- Écart supérieur à 20 min : début d'une nouvelle session (pause, réunion) ; la première pièce d'une session n'a pas de temps mesuré.
- Écart inférieur à 5 s : validation en lot, non retenue (signalée dans le rapport).
- On publie la **médiane** et le **90ᵉ centile**, peu sensibles aux valeurs extrêmes, plutôt que la moyenne.

**Heures d'indexation** d'un indexeur : somme de ses temps mesurés + son temps médian pour chaque pièce non mesurée. **Cadence** = validations / heures d'indexation.

**Attente avant indexation** : de la fin de l'analyse automatique (à défaut, du dépôt) au premier enregistrement. **Délai de validation** : du premier enregistrement à la validation. **Délai de versement** : de la validation au versement.

## Qualité

- **Champs corrigés** (pièces validées) : champs `mulcv:*` dont la valeur validée (`scan_job.approved_snapshot`) diffère de la proposition de l'analyse (`scan_job.applied`). Les propriétés posées par le serveur (`sha256`, statuts, `classCode`, `classPath`, `planVersion`…) ne comptent pas.
- **Acceptées sans correction** : part des pièces indexées sans aucun champ corrigé — mesure directe de l'utilité de l'analyse automatique.
- **Reconnaissance automatique** : part des analyses dont la nature proposée n'est pas « inconnu ».
- **Anomalies** : avertissements de l'analyse (`SURFACE_CONFLICT`, `DESTINATION_REQUIRES_REVIEW`, `MUNICIPALITY_DIFFERS_FROM_COTE`, `CONFLICT_<champ>`, `DOSSIER_ID_CONFLICT`).
- **Indexées non validées** : pièces dont les métadonnées sont enregistrées mais pas encore validées par l'archiviste.

## Comparaisons

- Point du jour : moyenne des 10 derniers jours ouvrés **avec indexation**, à la même heure.
- Hebdomadaire : même période de la semaine précédente.
- Une variation n'est commentée que si elle est significative : a + b ≥ 20 et |a − b| / √(a + b) ≥ 2.
- Résorption du stock : stock / (indexées par jour − nouvelles par jour), moyennes des 10 derniers jours d'indexation ; non calculée si le stock augmente.

## Limites

- Le service Scan ne journalise ni l'ouverture d'une pièce ni son enregistrement : sans historique des versions, seul le dernier enregistrement d'une pièce non validée est daté exactement.
- Les documents indexés hors du parcours Scan (formulaires du portail, autres sites) ne sont pas suivis par ce rapport.
- Le nombre de pages n'est pas enregistré dans le SAE ; il n'est pas utilisé ici.
