"""
Module d'accès aux APIs cadastrales.

Responsabilités :
- Géocoder une adresse postale via api-adresse.data.gouv.fr
- Récupérer les informations cadastrales (surface, géométrie, référence)
  via apicarto.ign.fr/api/cadastre/parcelle
- Retourner un objet Parcelle structuré
"""

import logging
import requests
from src.api.models import Parcelle

logger = logging.getLogger(__name__)


def geocoder_adresse(adresse: str) -> tuple[float, float]:
    """
    Convertit une adresse postale en coordonnées GPS (longitude, latitude).
    Utilise l'API adresse.data.gouv.fr — gratuite, sans clé.

    Retourne : (longitude, latitude)
    """
    try:
        r = requests.get(
            "https://api-adresse.data.gouv.fr/search/",
            params={"q": adresse, "limit": 1},
            timeout=10,
        )
        r.raise_for_status()
        features = r.json().get("features", [])
        if not features:
            raise ValueError(f"Adresse introuvable : {adresse!r}")
        lon, lat = features[0]["geometry"]["coordinates"]
        return lon, lat
    except Exception as e:
        logger.error("Erreur géocodage adresse %r : %s", adresse, e)
        raise


def suggerer_adresses(query: str, limit: int = 5) -> list[dict]:
    """
    Retourne jusqu'à `limit` suggestions d'adresses depuis api-adresse.data.gouv.fr.
    Chaque suggestion : {"label": str, "lon": float, "lat": float}
    """
    try:
        r = requests.get(
            "https://api-adresse.data.gouv.fr/search/",
            params={"q": query, "limit": limit},
            timeout=10,
        )
        r.raise_for_status()
        suggestions = []
        for f in r.json().get("features", []):
            lon, lat = f["geometry"]["coordinates"]
            suggestions.append({
                "label": f["properties"].get("label", ""),
                "lon": lon,
                "lat": lat,
            })
        return suggestions
    except Exception as e:
        logger.error("Erreur suggestions adresse %r : %s", query, e)
        return []


def _props_to_parcelle(props: dict, geometrie: dict) -> Parcelle:
    """Convertit les properties IGN en objet Parcelle."""
    return Parcelle(
        ref_cadastrale=props.get("idu", props.get("numero", "")),
        surface_m2=float(props.get("contenance", 0)),
        commune=props.get("nom_com", ""),
        code_insee=props.get("code_insee", props.get("code_dep", "") + props.get("code_com", "")),
        geometrie=geometrie,
    )


def get_parcelle_by_coords(lon: float, lat: float) -> Parcelle:
    """
    Récupère les données cadastrales de la parcelle à partir de coordonnées GPS.
    Utilise apicarto.ign.fr/api/cadastre/parcelle avec filtrage géométrique.
    """
    import json as _json
    try:
        geom = _json.dumps({"type": "Point", "coordinates": [lon, lat]})
        r = requests.get(
            "https://apicarto.ign.fr/api/cadastre/parcelle",
            params={"geom": geom},
            timeout=10,
        )
        r.raise_for_status()
        features = r.json().get("features", [])
        if not features:
            raise ValueError(f"Aucune parcelle trouvée aux coordonnées ({lat}, {lon})")
        data = features[0]
        return _props_to_parcelle(data["properties"], data["geometry"])
    except Exception as e:
        logger.error("Erreur récupération parcelle (%s, %s) : %s", lat, lon, e)
        raise


def get_parcelle_by_address(adresse: str) -> Parcelle:
    """
    Pipeline complet : adresse postale → objet Parcelle.
    Enchaîne le géocodage et la récupération cadastrale.
    """
    lon, lat = geocoder_adresse(adresse)
    parcelle = get_parcelle_by_coords(lon, lat)
    parcelle.adresse = adresse
    return parcelle


def _parser_ref_cadastrale(ref: str) -> dict:
    """
    Décompose une référence cadastrale au format IDU (14 caractères).
    ex: "75056000BX0042" → code_insee=75056, section=BX, numero=0042

    Format : [code_insee 5][com_abs 3][section 2][numero 4]
    """
    if len(ref) != 14:
        raise ValueError(f"Référence cadastrale invalide (doit faire 14 caractères) : {ref!r}")
    return {
        "code_insee": ref[0:5],
        "section": ref[8:10],
        "numero": ref[10:14],
    }


def get_parcelle_by_ref(ref_cadastrale: str) -> Parcelle:
    """
    Récupère une parcelle directement depuis sa référence cadastrale IDU.
    ex: "75056000BX0042"
    """
    try:
        params = _parser_ref_cadastrale(ref_cadastrale)
        r = requests.get(
            "https://apicarto.ign.fr/api/cadastre/parcelle",
            params=params,
            timeout=10,
        )
        r.raise_for_status()
        features = r.json().get("features", [])
        if not features:
            raise ValueError(f"Parcelle introuvable : {ref_cadastrale!r}")
        data = features[0]
        return _props_to_parcelle(data["properties"], data["geometry"])
    except Exception as e:
        logger.error("Erreur récupération parcelle %r : %s", ref_cadastrale, e)
        raise


if __name__ == "__main__":
    # Test manuel — parcelle de référence Paris 16e
    logging.basicConfig(level=logging.INFO)
    p = get_parcelle_by_ref("75056000BX0042")
    print(p)
