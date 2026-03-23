"""
Tests unitaires pour le pipeline zonR.

Parcelle de référence pour les tests : 75056000BX0042 (Paris 16e).
"""

import pytest
from unittest.mock import MagicMock

from src.api.models import Parcelle, ZonePLU
from src.parser.rules_model import ReglesUrbanisme
from src.engine.capacity import calculer_capacite, EtudeCapacitaire

# --- Fixtures ---

@pytest.fixture
def parcelle_test() -> Parcelle:
    """Parcelle de référence Paris 16e — géométrie Point (fallback reculs)."""
    return Parcelle(
        ref_cadastrale="75056000BX0042",
        surface_m2=500.0,
        commune="Paris 16e",
        code_insee="75116",
        geometrie={"type": "Point", "coordinates": [2.2769, 48.8566]},
    )


@pytest.fixture
def parcelle_polygon() -> Parcelle:
    """Parcelle 500 m² avec géométrie Polygon (~22m × 22m) — permet le calcul shapely des reculs."""
    # Carré ~22.4m × 22.4m centré sur Paris 16e
    # 22.4m lat ≈ 0.0002018°  |  22.4m lon ≈ 0.0003018° (à 48.85°N)
    return Parcelle(
        ref_cadastrale="75056000BX0042",
        surface_m2=500.0,
        commune="Paris 16e",
        code_insee="75116",
        geometrie={
            "type": "Polygon",
            "coordinates": [[
                [2.27690, 48.85660],
                [2.27720, 48.85660],
                [2.27720, 48.85680],
                [2.27690, 48.85680],
                [2.27690, 48.85660],
            ]],
        },
    )


@pytest.fixture
def regles_completes() -> ReglesUrbanisme:
    return ReglesUrbanisme(
        zone="UA",
        emprise_sol_max_pct=60.0,
        hauteur_max_m=18.0,
        surface_plancher_max_m2=None,
        usages_autorises=["habitation", "commerce"],
        usages_interdits=["industrie"],
        contraintes=[],
        recul_voirie_m=3.0,
        recul_limites_m=None,
    )


@pytest.fixture
def regles_sans_valeurs() -> ReglesUrbanisme:
    """Règles avec toutes les valeurs numériques à None — teste les défauts."""
    return ReglesUrbanisme(
        zone="UB",
        emprise_sol_max_pct=None,
        hauteur_max_m=None,
        surface_plancher_max_m2=None,
        usages_autorises=[],
        usages_interdits=[],
        contraintes=[],
    )


@pytest.fixture
def regles_zone_n() -> ReglesUrbanisme:
    """Zone naturelle — constructibilité nulle."""
    return ReglesUrbanisme(
        zone="N",
        emprise_sol_max_pct=0.0,
        hauteur_max_m=4.0,
        surface_plancher_max_m2=None,
        usages_autorises=[],
        usages_interdits=["toute construction"],
        contraintes=["zone naturelle protégée"],
    )


# --- Tests calcul capacitaire ---

class TestCalculerCapacite:

    def test_calcul_nominal(self, parcelle_test, regles_completes):
        # Point geometry → fallback reculs, emprise nette = emprise brute
        etude = calculer_capacite(parcelle_test, regles_completes)
        assert etude.emprise_brute_m2 == 300.0      # 500 * 60%
        assert etude.hauteur_max_m == 18.0
        assert etude.nb_niveaux_estimes == 6         # 18 / 3
        assert etude.surface_plancher_max_m2 == 1800.0  # 300 * 6
        assert etude.nb_logements_estimes_min > 0
        assert etude.nb_logements_estimes_max >= etude.nb_logements_estimes_min

    def test_valeurs_par_defaut_appliquees(self, parcelle_test, regles_sans_valeurs):
        etude = calculer_capacite(parcelle_test, regles_sans_valeurs)
        assert etude.emprise_brute_m2 == 300.0   # 500 * 60% défaut
        assert etude.hauteur_max_m == 12.0
        assert etude.nb_niveaux_estimes == 4        # 12 / 3
        # 2 valeurs par défaut (emprise, hauteur) + 1 hypothèse T2/T3
        assert len(etude.hypotheses) == 3

    def test_zone_naturelle_alerte(self, parcelle_test, regles_zone_n):
        etude = calculer_capacite(parcelle_test, regles_zone_n)
        assert any("Zone N" in a for a in etude.alertes)

    def test_alerte_recul_voirie(self, parcelle_test, regles_completes):
        etude = calculer_capacite(parcelle_test, regles_completes)
        assert any("voirie" in a.lower() for a in etude.alertes)

    def test_surface_plancher_explicite(self, parcelle_test):
        regles = ReglesUrbanisme(
            zone="UC",
            emprise_sol_max_pct=50.0,
            hauteur_max_m=10.0,
            surface_plancher_max_m2=400.0,  # COS explicite
            usages_autorises=[],
            usages_interdits=[],
            contraintes=[],
        )
        etude = calculer_capacite(parcelle_test, regles)
        assert etude.surface_plancher_max_m2 == 400.0  # valeur PLU respectée

    def test_logements_positifs(self, parcelle_test, regles_completes):
        etude = calculer_capacite(parcelle_test, regles_completes)
        assert etude.nb_logements_estimes_min >= 0
        assert etude.nb_logements_estimes_max >= 0

    def test_emprise_non_reglementee_100pct(self, parcelle_test):
        regles = ReglesUrbanisme(
            zone="UC",
            emprise_sol_max_pct=None,
            hauteur_max_m=9.0,
            surface_plancher_max_m2=None,
            emprise_non_reglementee=True,
        )
        etude = calculer_capacite(parcelle_test, regles)
        # 100% du polygone (les reculs/EV/parking limitent)
        assert etude.emprise_brute_m2 == 500.0   # 500 * 100%
        # L'alerte doit être dans alertes (pas dans hypotheses)
        assert any("non réglementée" in a.lower() for a in etude.alertes)
        assert not any("non réglementée" in h.lower() for h in etude.hypotheses)

    def test_hypothese_t2_t3_toujours_presente(self, parcelle_test, regles_completes):
        etude = calculer_capacite(parcelle_test, regles_completes)
        assert any("T2" in h and "T3" in h for h in etude.hypotheses)

    def test_cos_explicite_niveaux_coherents(self, parcelle_test):
        regles = ReglesUrbanisme(
            zone="UC",
            emprise_sol_max_pct=50.0,
            hauteur_max_m=10.0,
            surface_plancher_max_m2=500.0,  # COS = 2 niveaux sur 250m² emprise
        )
        etude = calculer_capacite(parcelle_test, regles)
        assert etude.surface_plancher_max_m2 == 500.0
        # nb_niveaux cohérent avec COS : 500 / 250 = 2
        assert etude.nb_niveaux_estimes == 2

    def test_reculs_reduisent_emprise_sur_polygon(self, parcelle_polygon):
        """Avec une géométrie Polygon, les reculs doivent réduire l'emprise via shapely."""
        regles_sans_recul = ReglesUrbanisme(
            zone="UA", emprise_sol_max_pct=100.0, hauteur_max_m=9.0,
            surface_plancher_max_m2=None,
        )
        regles_avec_recul = ReglesUrbanisme(
            zone="UA", emprise_sol_max_pct=100.0, hauteur_max_m=9.0,
            surface_plancher_max_m2=None, recul_limites_m=3.0,
        )
        etude_sans = calculer_capacite(parcelle_polygon, regles_sans_recul)
        etude_avec = calculer_capacite(parcelle_polygon, regles_avec_recul)
        # L'emprise après reculs doit être inférieure à l'emprise brute
        assert etude_avec.emprise_apres_reculs_m2 < etude_avec.emprise_brute_m2
        # Et l'emprise nette avec recul < emprise nette sans recul
        assert etude_avec.emprise_sol_max_m2 < etude_sans.emprise_sol_max_m2

    def test_stationnement_reduit_sp(self, parcelle_polygon):
        """Le stationnement doit réduire la surface de plancher finale."""
        regles_sans_stat = ReglesUrbanisme(
            zone="UA", emprise_sol_max_pct=80.0, hauteur_max_m=9.0,
            surface_plancher_max_m2=None,
        )
        regles_avec_stat = ReglesUrbanisme(
            zone="UA", emprise_sol_max_pct=80.0, hauteur_max_m=9.0,
            surface_plancher_max_m2=None, stationnement_par_logt=1.0,
        )
        etude_sans = calculer_capacite(parcelle_polygon, regles_sans_stat)
        etude_avec = calculer_capacite(parcelle_polygon, regles_avec_stat)
        assert etude_avec.surface_parking_m2 > 0
        assert etude_avec.surface_plancher_max_m2 < etude_sans.surface_plancher_max_m2

    def test_espaces_verts_reduisent_sp(self, parcelle_polygon):
        """Les espaces verts obligatoires doivent réduire la surface de plancher."""
        regles_sans_ev = ReglesUrbanisme(
            zone="UA", emprise_sol_max_pct=80.0, hauteur_max_m=9.0,
            surface_plancher_max_m2=None,
        )
        regles_avec_ev = ReglesUrbanisme(
            zone="UA", emprise_sol_max_pct=80.0, hauteur_max_m=9.0,
            surface_plancher_max_m2=None, espace_vert_min_pct=20.0,
        )
        etude_sans = calculer_capacite(parcelle_polygon, regles_sans_ev)
        etude_avec = calculer_capacite(parcelle_polygon, regles_avec_ev)
        assert etude_avec.surface_ev_m2 == 500.0 * 0.20  # 100 m²
        assert etude_avec.surface_plancher_max_m2 < etude_sans.surface_plancher_max_m2

    def test_surface_t2_t3_custom(self, parcelle_test):
        """Les paramètres surface_t2_m2 et surface_t3_m2 personnalisés sont respectés."""
        regles = ReglesUrbanisme(
            zone="UA", emprise_sol_max_pct=60.0, hauteur_max_m=9.0,
            surface_plancher_max_m2=None,
        )
        etude_std = calculer_capacite(parcelle_test, regles)
        etude_custom = calculer_capacite(parcelle_test, regles,
                                         surface_t2_m2=40.0, surface_t3_m2=55.0)
        # Avec des logements plus petits, on en estime plus
        assert etude_custom.nb_logements_estimes_max > etude_std.nb_logements_estimes_max
