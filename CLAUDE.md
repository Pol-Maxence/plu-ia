# PLU·IA — Contexte projet

## Ce que fait ce projet
SaaS qui génère des études capacitaires réglementaires depuis une adresse ou référence cadastrale.
Pipeline : adresse → API Cadastre IGN → API Géoportail Urbanisme → extraction PLU (LLM Claude) → calcul capacitaire → rapport PDF.

## Stack
- Python 3.11.9 / Windows
- Anthropic API (`claude-sonnet-4-20250514`) — extraction règles PLU
- Streamlit + streamlit-folium — interface web + carte interactive
- ReportLab — génération PDF
- PyMuPDF (fitz) — extraction texte PDF (37× plus rapide que pdfminer)
- Shapely — reculs géométriques, union multi-parcelles
- APIs : apicarto.ign.fr (cadastre + GPU), api-adresse.data.gouv.fr, geoportail-urbanisme.gouv.fr

## Structure des dossiers
```
src/api/        → cadastre.py, geoportail.py, models.py (Parcelle, ZonePLU)
src/parser/     → rules_model.py (ReglesUrbanisme 14 champs), plu_extractor.py (LLM)
src/engine/     → capacity.py (EtudeCapacitaire, pipeline séquentiel)
src/report/     → pdf_generator.py (carte IGN, tableaux, schéma volumétrique)
src/main.py     → CLI run() / run_multi()
app.py          → Streamlit (446 lignes)
scripts/validate_extraction.py → validation extraction vs références connues
tests/fixtures/cache/          → cache PLU text + JSON LLM (validate_extraction)
```

## Conventions
- Type hints partout, docstrings en **français**, variables en **anglais**
- `logger.error(...)` toujours avant `raise`
- Fallbacks géométriques gracieux (retourner 0.0 plutôt que crasher)

## Parcelles de test
| Référence | Localisation | Zone | Usage |
|-----------|-------------|------|-------|
| `75056000BX0042` | Paris 16e | — | Tests unitaires (géométrie Point) |
| `603370000A1140` + 3 | Lachelle | UV7.1, 2 002 m² | Multi-parcelles ; réf. Nacarat 21 lgts |
| `60612000AX0324` | Senlis | UCb, 703 m² | Zone avec CES explicite |
| `62041000AB0570` | Arras | UAa+ | PLUi intercommunal ; Nacarat 90%/19 m |
| `60173000AC0029` | Cramoisy | 1AUm | Zone avec espace "1 AUm" dans le PLU |

## Moteur capacitaire — pipeline séquentiel (`src/engine/capacity.py`)
1. **Emprise brute** = surface × emprise_pct (`emprise_non_reglementee=True` → 100%, défaut 60%)
2. **Reculs** = buffer Shapely inward (`recul_deg = recul_m / 111_000`) → ratio × surface officielle
3. **Espaces verts** = surface × espace_vert_min_pct déduit de l'emprise
4. **Parking (itératif)** = lgt_prov × stationnement × 16 m²/place déduit de la SP
5. **SP max** = emprise_nette × nb_niveaux (plancher 5% à chaque étape)
6. **Logements** = SP habitable (×0.75) / 65 m² (min T3) à / 50 m² (max T2)

Constantes clés : `_HAUTEUR_NIVEAU_M=3.0`, `_EMPRISE_MAX_DEFAUT_PCT=60.0`, `_SURFACE_PAR_PLACE_M2=16.0`

Multi-parcelles : `unary_union` avant le buffer pour la vraie géométrie fusionnée.

## Extraction PLU — pipeline complet

### Cache 3 niveaux (`tests/fixtures/cache/` pour benchmark, même logique dans main)
| Fichier | Contenu | Quand recalculé |
|---------|---------|-----------------|
| `{ref}_full_plu.txt` | Texte complet PDF avec marqueurs `\f[PAGE N]` | Jamais (téléchargement réseau) |
| `{ref}_toc.json` | Signets PDF `[[level, title, page], ...]` | Jamais (avec le PDF) |
| `{ref}_section.txt` | Extrait zone (≤ 40 000 chars) | Après fix d'extraction |
| `{ref}_regles.json` | Résultat LLM final | Après fix de prompt |

**Reset partiel après fix :** supprimer `_section.txt` + `_regles.json`, garder `_full_plu.txt` + `_toc.json`.

### Stratégie d'extraction de section (`extraire_section_zone`)
1. **TOC signets PDF** (`doc.get_toc()` non vide) → `extraire_section_via_toc`
   - Si la zone a des **sous-signets enfants** correspondant aux rubriques clés (emprise, hauteur, stationnement, implantation, espaces, `dispositions réglementaires`, `cas général`) → extraction ciblée par sous-section, triée avec "dispositions réglementaires / cas général" **en premier** (contient les valeurs numériques dans les PLUi type Bordeaux Métropole)
   - Sinon → plage de pages zone complète (cap 25 000 chars)
2. **Regex fallback** (PDF sans signets) → 7 patterns + 3 fallbacks (zone exacte → stripped → base → 8 000 premiers chars)

### Articles clés (`_extraire_articles_cles`)
Format 1 — PLU classique numéroté : articles **6** (recul voirie), **7** (recul limites), **8** (entre bâtiments), **9** (emprise), **10** (hauteur), **11** (aspect), **12** (stationnement), **13** (espaces verts), **14** (COS)

Format 1b — PLUi rubrique variable : `ARTICLE {zone} N : [≤80 chars]{rubrique}` — titres IMPLANTATION PAR RAPPORT AUX VOIES/LIMITES, HAUTEUR, EMPRISE, STATIONNEMENT, ESPACES

Format 2 — titres libres en clair ("Emprise au sol", "Hauteur maximale", etc.)

**`_zone_re(zone)`** : insère `\s*` aux frontières chiffre↔lettre → couvre "ZONE 1 AUm" quand l'API retourne "1AUm".

### LLM (`extraire_regles_plu`)
- **Premier appel** : `max_chars=15 000` (coût minimal)
- **Retry automatique à 40 000 chars** si tous les champs clés (emprise, hauteur, recul, espace vert) sont null ET texte plus long — évite le coût inutile sur 95% des documents
- `max_tokens=2 000`. Retourne verbatims (citations exactes) par champ.

## Points d'attention critiques
- **SSL Geoportail** : `verify=False` + `urllib3.disable_warnings()` — uniquement pour `geoportail-urbanisme.gouv.fr`
- **Cache stale** : après un fix d'extraction, supprimer `*_section.txt` + `*_regles.json` (garder `_full_plu.txt` et `_toc.json`). L'ancien `_plu_text.txt` est obsolète.
- **PLUi profonds** (ex: Bordeaux Métropole UM16) : les valeurs numériques sont en "2.2 Dispositions réglementaires", pas en "2.1 Définitions". Le tri par priorité dans `_RUBRIQUE_RE` place ces bookmarks en premier.
- **Hauteur dans les OAP** : certains PLUi (Cramoisy 1AUm) définissent la hauteur dans les OAP, pas le règlement → LLM retourne `null`, limitation acceptable
- **`nomfic`** peut contenir `#page=N` — inoffensif pour requests
- **Verbatims** : citations tronquées à 130 chars dans `_cell_avec_verbatim()`, échappées XML (`&amp;` etc.)

## Validation extraction
```bash
python scripts/validate_extraction.py              # utilise le cache
python scripts/validate_extraction.py --refresh    # tout recalculer
python scripts/validate_extraction.py --refresh Arras
```

## Examples
```
examples/Etude capacitaire.docx  → spec métier
examples/{ville}/                → études PDF de référence (Arras, Lachelle, Senlis…)
```

---

## Next steps

### Qualité extraction
- [ ] Migrer `ReglesUrbanisme` vers **Pydantic** pour validation et coercition des types à la sortie LLM
- [ ] Compléter les `expected` dans `validate_extraction.py` pour tous les sites (Senlis, Cramoisy, Aumont, Baron) et viser 100% en CI

### Moteur capacitaire
- [ ] Recul asymétrique : recul voirie sur façade principale uniquement (buffer directionnel via géométrie)
- [ ] Bonus hauteur/densité : capturer les dérogations PLU (R+1 si BBC, logements sociaux…) dans `ReglesUrbanisme`

### Interface & rapport
- [ ] Édition des règles extraites dans l'UI avant calcul (correction LLM par l'utilisateur)
- [ ] Résumé interactif dans Streamlit (métriques + tableau) avant téléchargement PDF
- [ ] Variantes de scénarios dans le PDF (conservateur / référence / maximal)
- [ ] Logo cabinet personnalisable en en-tête PDF

### Infrastructure
- [ ] Cache PLU local sur disque avec TTL ~30 jours (éviter re-téléchargement pour même commune)
- [ ] API REST FastAPI pour intégration tierce (promoteurs, logiciels métier)
- [ ] Authentification + quotas pour mode SaaS multi-utilisateurs
- [ ] Monitoring qualité extraction : taux de succès par champ, fallbacks déclenchés
