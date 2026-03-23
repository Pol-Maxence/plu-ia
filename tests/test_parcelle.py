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
    """Parcelle de référence Paris 16e — 75056000BX0042."""
    return Parcelle(
        ref_cadastrale="75056000BX0042",
        surface_m2=500.0,
        commune="Paris 16e",
        code_insee="75116",
        geometrie={"type": "Point", "coordinates": [2.2769, 48.8566]},
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
        etude = calculer_capacite(parcelle_test, regles_completes)
        assert etude.emprise_sol_max_m2 == 300.0   # 500 * 60%
        assert etude.hauteur_max_m == 18.0
        assert etude.nb_niveaux_estimes == 6        # 18 / 3
        assert etude.surface_plancher_max_m2 == 1800.0  # 300 * 6
        assert etude.nb_logements_estimes_min > 0
        assert etude.nb_logements_estimes_max >= etude.nb_logements_estimes_min

    def test_valeurs_par_defaut_appliquees(self, parcelle_test, regles_sans_valeurs):
        etude = calculer_capacite(parcelle_test, regles_sans_valeurs)
        # Doit utiliser les valeurs par défaut (60% emprise, 12m hauteur)
        assert etude.emprise_sol_max_m2 == 300.0   # 500 * 60% défaut
        assert etude.hauteur_max_m == 12.0
        assert etude.nb_niveaux_estimes == 4        # 12 / 3
        assert len(etude.hypotheses) == 2           # 2 valeurs par défaut

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
