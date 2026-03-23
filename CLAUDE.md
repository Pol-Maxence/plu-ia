# PLU·IA — Contexte projet

## Ce que fait ce projet
SaaS qui génère automatiquement des études capacitaires réglementaires
à partir d'une adresse ou référence cadastrale française.
Pipeline : adresse → API Géoportail → extraction règles PLU (LLM) → calcul
capacitaire → rapport PDF.

## Stack
- Python 3.11.9 / Windows
- Anthropic API (claude-sonnet-4-20250514) pour l'analyse PLU
- API Géoportail de l'Urbanisme (geoportail-urbanisme.gouv.fr)
- API Cadastre / API Carto IGN (apicarto.ign.fr)
- ReportLab pour génération PDF
- PyMuPDF (fitz) pour extraction texte PDF — remplace pdfminer (37× plus rapide)
- Shapely pour calculs géométriques (reculs, union multi-parcelles)
- Streamlit + streamlit-folium pour l'interface web

## Structure des dossiers
- src/api/       → appels APIs externes
- src/parser/    → extraction et interprétation des règles PLU
- src/engine/    → calcul capacitaire
- src/report/    → génération PDF
- tests/         → tests unitaires

## Conventions
- Type hints partout
- Docstrings en français
- Variables en anglais, commentaires en français
- Erreurs : toujours logger avant de raise

## Parcelles de test
- `75056000BX0042` — Paris 16e (tests unitaires, géométrie Point)
- `603370000A1140 + 603370000A1142 + 603370000A1139 + 603370000A1141` — Lachelle UV7.1, 2002 m² (test multi-parcelles, référence Nacarat 1365 m²/21 logements)
- `60612000AX0324` — Senlis UCb, 703 m² (test zone avec CES explicite)

## Moteur capacitaire — logique de calcul

Pipeline séquentiel dans `src/engine/capacity.py` :
1. **Emprise brute** = surface × CES% (100% si `emprise_non_reglementee=True`, 60% par défaut)
2. **Après reculs** = buffer shapely inward sur union des polygones × ratio surface officielle
3. **Après EV** = emprise après reculs − (surface × espace_vert_min_pct%)
4. **Parking (itératif)** = estimation provisoire logements → nb_places × 16 m²/place
5. **Emprise nette** = après EV − parking (plancher 5% de l'emprise brute)
6. **SP max** = emprise nette × nb_niveaux

Règles importantes :
- `emprise_non_reglementee=True` → 100% (pas 80%), les reculs/EV/parking font le travail
- `_SURFACE_PAR_PLACE_M2 = 16.0` (place + manœuvre parking aérien)
- Multi-parcelles : utiliser `shapely.ops.unary_union` avant le buffer pour avoir la vraie géométrie fusionnée

## Extraction PLU — points d'attention

- Les PLUi (format tableau) ont souvent les titres et valeurs sur des lignes séparées, ex: `zone\nUV7.1`
- Le pattern regex `(?:^|\n)\s*(?:ZONE|Zone|zone)\s*\n\s*{zone}\b` couvre ce cas
- `nomfic` retourné par l'API GPU peut contenir un fragment `#page=N` — inoffensif pour requests
- PyMuPDF extrait différemment de pdfminer (pas de fusion de lignes) → les regex doivent tolérer les sauts de ligne

## Parcelle de test (tests unitaires)
75056000BX0042 — Paris 16e (utiliser pour tous les tests unitaires pytest)