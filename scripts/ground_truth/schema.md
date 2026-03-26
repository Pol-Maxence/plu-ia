# Format ground truth — Référence de saisie

Chaque fichier JSON correspond à **une parcelle** (ou un groupe de parcelles).
Le nom du fichier = référence cadastrale (ou refs jointes par `_`).

## Structure

```json
{
  "ref": "62041000AB0570",          // référence cadastrale principale
  "source": "KelFoncier",           // "KelFoncier" | "manuel" | "KelFoncier+manuel"
  "date": "2024-07",                // mois de vérification (YYYY-MM)
  "commune": "Arras",
  "zone": "UAa+",                   // code zone retourné par l'API GPU

  "champs": {

    "emprise_sol_max_pct": {
      "valeur": 90.0,               // float | null
      "non_reglementee": false,     // true si PLU dit "non réglementé"
      "condition": null,            // ex: "secteur UAa uniquement"
      "source_oap": false           // true si règle dans OAP, pas le règlement
    },

    "hauteur_max_m": {
      "valeur": 19.0,               // float | null
      "type": "faitage",            // "faitage" | "egout" | "acrotere" | null
      "valeur_egout": null,         // si type=faitage et egout différent
      "condition": null,            // ex: "secteur UAa"
      "source_oap": false
    },

    "recul_voirie_m": {
      "valeur": null,               // float | null — null si alignement
      "alignement": true,           // true si implantation à l'alignement des voisins
      "formule": null               // ex: "L/2" si règle de prospect
    },

    "recul_limites_m": {
      "valeur": null,               // float | null — null si formule
      "formule": "H/2",             // ex: "H/2", "H/4 min 2m" | null
      "minimum_m": null             // plancher en mètres si formule avec minimum
    },

    "stationnement_par_logt": {
      "accession": 1.25,            // float | null — accession libre
      "social": 1.0,                // float | null — null = règle nationale (1 pl/logt)
      "velos": null                 // float | null — places vélos
    },

    "espace_vert_min_pct": {
      "valeur": 10.0,               // float | null
      "pleine_terre_pct": null,     // sous-ensemble pleine terre obligatoire
      "biotope": null               // coefficient de biotope (CBS)
    },

    "source_oap_global": false,     // true si les règles principales sont dans les OAP
    "notes": ""                     // observations libres (règles conditionnelles, etc.)
  }
}
```

## Règles de saisie

- `null` = information non disponible / non testée (≠ 0)
- `0` = valeur explicitement nulle dans le PLU (rare)
- Les champs avec `"source": "KelFoncier"` viennent de l'import Excel automatique
- Les champs ajoutés manuellement → changer `"source"` en `"KelFoncier+manuel"`

## Champs disponibles depuis l'Excel KelFoncier

| Champ | Onglet | Libellé Excel |
|-------|--------|---------------|
| `zone` | Règlement, ligne 1 | "Sous-zone principale" |
| `emprise_sol_max_pct.valeur` | Faisabilité, ligne 2 | "Emprise au sol autorisée" |
| `nb_niveaux` (proxy hauteur) | Faisabilité, ligne 11 | "Nombre de niveaux" |
| `recul_limites_m.valeur` | Faisabilité, ligne 6 | "Limite séparative latérale (b)" |
| `stationnement_par_logt.accession` | Faisabilité, ligne 22 | "Nombre de places exigées" |
| `contraintes` | Info parcelle, lignes 8-19 | ABF, Loi littoral, ZPPA... |

## Champs à saisir manuellement (non disponibles dans l'Excel)

- `hauteur_max_m.valeur` (en mètres)
- `hauteur_max_m.type` (faitage / egout / acrotere)
- `recul_voirie_m` / `recul_voirie_alignement`
- `recul_limites_m.formule` (ex: "H/2")
- `espace_vert_min_pct`
- `source_oap_global`
