# zonR

Génération automatique d'études capacitaires réglementaires à partir d'une adresse ou référence cadastrale française.

## Installation

```bash
# Créer et activer l'environnement virtuel
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Mac/Linux

# Installer les dépendances
pip install -r requirements.txt
```

## Configuration

Créer un fichier `.env` à la racine :

```
ANTHROPIC_API_KEY=sk-ant-...
```

## Utilisation

```bash
python -m src.main "15 rue de la Paix, Paris"
```

Le rapport PDF est généré dans le dossier courant sous le nom `rapport.pdf`.

## Structure

```
src/
  api/          # Appels APIs externes (Géoportail, Cadastre)
  parser/       # Extraction des règles PLU via LLM
  engine/       # Calcul capacitaire
  report/       # Génération PDF
tests/          # Tests unitaires (pytest)
data/samples/   # PLU exemples pour les tests
```

## Parcelle de test

`75056000BX0042` — Paris 16e
