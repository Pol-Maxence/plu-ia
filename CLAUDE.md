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

## Parcelle de test
75056000BX0042 — Paris 16e (utiliser pour tous les tests)
```