"""
zonR — Interface web Streamlit.

Lancement :
    streamlit run app.py
"""

import tempfile

import folium
import streamlit as st
from dotenv import load_dotenv
from streamlit_folium import st_folium

load_dotenv()

# ---------------------------------------------------------------------------
# Constantes cartographiques
# ---------------------------------------------------------------------------

_IGN_WMTS = (
    "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal"
    "&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=image/png"
)
_GPU_WMS = "https://wxs.ign.fr/gpu/geoportail/r/wms"

# ---------------------------------------------------------------------------
# Configuration page
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="zonR — Étude capacitaire",
    page_icon="🏗️",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Initialisation session state
# ---------------------------------------------------------------------------

defaults = {
    "suggestions": [],
    "adresse_query": "",
    "selected_parcelles": [],
    "zones_geojson": [],
    "multi_mode": False,
    "map_center": [46.5, 2.5],
    "map_zoom": 6,
    "last_click": None,
    "skip_next_click": False,
    "map_version": 0,  # incrémenté à chaque déplacement programmatique de la carte
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


def _centroid(geom: dict, fallback_lat: float = None, fallback_lon: float = None) -> list[float]:
    """Retourne [lat, lon] du centroïde d'une géométrie GeoJSON (Point/Polygon/MultiPolygon)."""
    try:
        t = geom["type"]
        if t == "Point":
            return [geom["coordinates"][1], geom["coordinates"][0]]
        elif t == "Polygon":
            ring = geom["coordinates"][0]
            return [sum(c[1] for c in ring) / len(ring), sum(c[0] for c in ring) / len(ring)]
        elif t == "MultiPolygon":
            ring = geom["coordinates"][0][0]
            return [sum(c[1] for c in ring) / len(ring), sum(c[0] for c in ring) / len(ring)]
    except Exception:
        pass
    if fallback_lat is not None and fallback_lon is not None:
        return [fallback_lat, fallback_lon]
    return [46.5, 2.5]

# ---------------------------------------------------------------------------
# En-tête
# ---------------------------------------------------------------------------

st.title("zonR")
st.caption("Étude capacitaire réglementaire automatisée")
st.divider()

# ---------------------------------------------------------------------------
# Mise en page 2 colonnes
# ---------------------------------------------------------------------------

col_panel, col_carte = st.columns([1, 2], gap="large")

# ═══════════════════════════════════════════════════════════════════════════
# COLONNE GAUCHE — Panneau de recherche + sélection
# ═══════════════════════════════════════════════════════════════════════════

with col_panel:

    # --- Recherche par adresse ---
    st.subheader("Recherche")
    query = st.text_input(
        "Adresse",
        placeholder="14 rue de la Paix, Paris…",
        label_visibility="collapsed",
    )

    if st.button("Rechercher", use_container_width=True):
        if query.strip():
            from src.api.cadastre import suggerer_adresses
            with st.spinner("Recherche…"):
                st.session_state.suggestions = suggerer_adresses(query.strip())
                st.session_state.adresse_query = query.strip()
        else:
            st.session_state.suggestions = []

    # --- Liste déroulante des suggestions ---
    if st.session_state.suggestions:
        labels = [s["label"] for s in st.session_state.suggestions]
        choix = st.radio(
            "Sélectionnez une adresse",
            labels,
            key="radio_suggestion",
            label_visibility="collapsed",
        )
        suggestion_selectionnee = next(
            (s for s in st.session_state.suggestions if s["label"] == choix), None
        )

        if st.button("Confirmer cette adresse", use_container_width=True):
            if suggestion_selectionnee:
                from src.api.cadastre import get_parcelle_by_coords
                from src.api.geoportail import get_zonage_geojson
                with st.spinner("Identification de la parcelle…"):
                    try:
                        lon = suggestion_selectionnee["lon"]
                        lat = suggestion_selectionnee["lat"]
                        parcelle = get_parcelle_by_coords(lon, lat)
                        parcelle.adresse = suggestion_selectionnee["label"]
                        zone = get_zonage_geojson(lat, lon)

                        if st.session_state.multi_mode:
                            # Éviter les doublons
                            refs_existants = [p.ref_cadastrale for p in st.session_state.selected_parcelles]
                            if parcelle.ref_cadastrale not in refs_existants:
                                st.session_state.selected_parcelles.append(parcelle)
                                st.session_state.zones_geojson.append(zone)
                        else:
                            st.session_state.selected_parcelles = [parcelle]
                            st.session_state.zones_geojson = [zone]

                        # Centrer la carte sur la parcelle (fallback = coords BAN)
                        st.session_state.map_center = _centroid(
                            parcelle.geometrie, fallback_lat=lat, fallback_lon=lon
                        )
                        st.session_state.map_zoom = 19
                        st.session_state.map_version += 1
                        st.session_state.suggestions = []
                        st.session_state.skip_next_click = True
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erreur : {e}")

    st.divider()

    # --- Mode multi-parcelles ---
    multi = st.checkbox(
        "Mode multi-parcelles",
        value=st.session_state.multi_mode,
        help="Activez pour sélectionner plusieurs parcelles adjacentes.",
    )
    if multi != st.session_state.multi_mode:
        st.session_state.multi_mode = multi
        st.rerun()

    # --- Parcelles sélectionnées ---
    if st.session_state.selected_parcelles:
        st.markdown("**Sélection**")

        # Tags de suppression individuelle
        for i, p in enumerate(st.session_state.selected_parcelles):
            c1, c2 = st.columns([4, 1])
            c1.caption(p.ref_cadastrale)
            if c2.button("✕", key=f"del_{i}", help="Retirer"):
                st.session_state.selected_parcelles.pop(i)
                st.session_state.zones_geojson.pop(i)
                if not st.session_state.selected_parcelles:
                    st.session_state.map_center = [46.5, 2.5]
                    st.session_state.map_zoom = 6
                    st.session_state.map_version += 1
                st.rerun()

        # Bouton reset global
        if st.button("Tout effacer", use_container_width=True):
            st.session_state.selected_parcelles = []
            st.session_state.zones_geojson = []
            st.session_state.map_center = [46.5, 2.5]
            st.session_state.map_zoom = 6
            st.session_state.map_version += 1
            st.rerun()

        st.divider()

        # --- Infos de la dernière parcelle sélectionnée ---
        p = st.session_state.selected_parcelles[-1]
        zone_props = {}
        if st.session_state.zones_geojson and st.session_state.zones_geojson[-1]:
            zone_props = st.session_state.zones_geojson[-1].get("properties", {})

        st.markdown("**Détails**")
        st.markdown(f"Référence : `{p.ref_cadastrale}`")
        st.markdown(f"Commune : {p.commune}")
        st.markdown(f"Surface : {p.surface_m2:.0f} m²")
        zone_label = zone_props.get("libelong", zone_props.get("libelle", "—"))
        st.markdown(f"Zone PLU : **{zone_label}**")

    else:
        st.caption("Recherchez une adresse ou cliquez sur la carte pour sélectionner une parcelle.")

    st.divider()

    # --- Bouton Analyser ---
    has_selection = bool(st.session_state.selected_parcelles)
    if st.button(
        "Analyser →",
        type="primary",
        use_container_width=True,
        disabled=not has_selection,
    ):
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp_path = tmp.name
        tmp.close()

        parcelles = st.session_state.selected_parcelles
        try:
            with st.status("Analyse en cours…", expanded=True) as status:
                if len(parcelles) > 1:
                    from src.main import run_multi
                    refs = [p.ref_cadastrale for p in parcelles]
                    st.write(f"Récupération de {len(refs)} parcelle(s)…")
                    st.write("Récupération zonage PLU…")
                    st.write("Analyse PLU par IA…")
                    st.write("Calcul capacitaire…")
                    st.write("Génération du rapport PDF…")
                    run_multi(refs=refs, output=tmp_path)
                    safe_label = "+".join(refs)
                else:
                    from src.main import run
                    p = parcelles[0]
                    st.write("Récupération de la parcelle…")
                    st.write("Récupération zonage PLU…")
                    st.write("Analyse PLU par IA…")
                    st.write("Calcul capacitaire…")
                    st.write("Génération du rapport PDF…")
                    run(ref_cadastrale=p.ref_cadastrale, output=tmp_path)
                    safe_label = p.ref_cadastrale

                status.update(label="Analyse terminée ✓", state="complete")

            with open(tmp_path, "rb") as f:
                pdf_bytes = f.read()

            safe_label = safe_label.replace(" ", "_").replace("/", "-")
            st.success("Rapport généré.")
            st.download_button(
                label="⬇ Télécharger le rapport PDF",
                data=pdf_bytes,
                file_name=f"{safe_label}_Etude-capacitaire.pdf",
                mime="application/pdf",
                use_container_width=True,
            )

        except Exception as e:
            st.error(f"Erreur : {e}")

# ═══════════════════════════════════════════════════════════════════════════
# COLONNE DROITE — Carte interactive
# ═══════════════════════════════════════════════════════════════════════════

with col_carte:

    # Construction de la carte Folium
    m = folium.Map(
        location=st.session_state.map_center,
        zoom_start=st.session_state.map_zoom,
        tiles=None,
    )

    # Fond IGN Plan V2
    folium.TileLayer(
        tiles=_IGN_WMTS,
        attr="© IGN-F / Géoportail",
        name="Plan IGN",
        max_zoom=19,
    ).add_to(m)

    # Overlay zones PLU colorées (WMS GPU)
    folium.WmsTileLayer(
        url=_GPU_WMS,
        layers="URBANISME_PARCELLES:ZONE_URBA",
        fmt="image/png",
        transparent=True,
        opacity=0.45,
        name="Zones PLU",
        attr="© Géoportail de l'Urbanisme",
    ).add_to(m)

    # Overlay zones PLU sélectionnées (contour bleu pointillé)
    for zone_feat in st.session_state.zones_geojson:
        if zone_feat:
            zone_label = zone_feat.get("properties", {}).get("libelle", "")
            folium.GeoJson(
                zone_feat,
                style_function=lambda _: {
                    "fillColor": "#2980b9",
                    "color": "#1a6a99",
                    "weight": 2,
                    "fillOpacity": 0.10,
                    "dashArray": "6, 4",
                },
                tooltip=folium.Tooltip(f"Zone {zone_label}", sticky=False),
            ).add_to(m)

    # Overlay parcelles sélectionnées (rouge)
    for p in st.session_state.selected_parcelles:
        folium.GeoJson(
            {"type": "Feature", "geometry": p.geometrie, "properties": {}},
            style_function=lambda _: {
                "fillColor": "#e74c3c",
                "color": "#c0392b",
                "weight": 3,
                "fillOpacity": 0.35,
            },
            tooltip=folium.Tooltip(
                f"<b>{p.ref_cadastrale}</b><br>{p.commune} · {p.surface_m2:.0f} m²",
                sticky=True,
            ),
        ).add_to(m)

    # Affichage + capture du clic et du zoom courant
    map_data = st_folium(
        m,
        height=580,
        use_container_width=True,
        returned_objects=["last_clicked", "zoom"],
        key=f"carte_plu_{st.session_state.map_version}",
    )

    # Mémoriser le zoom choisi par l'utilisateur
    if map_data and map_data.get("zoom"):
        st.session_state.map_zoom = map_data["zoom"]

    # Traitement du clic carte
    if map_data and map_data.get("last_clicked"):
        click = map_data["last_clicked"]
        click_key = (round(click["lat"], 6), round(click["lng"], 6))
        # Ignorer le clic fantôme qui suit une confirmation d'adresse
        if st.session_state.skip_next_click:
            st.session_state.skip_next_click = False
            st.session_state.last_click = click_key
        elif click_key != st.session_state.last_click:
            st.session_state.last_click = click_key
            with st.spinner("Identification de la parcelle…"):
                try:
                    from src.api.cadastre import get_parcelle_by_coords
                    from src.api.geoportail import get_zonage_geojson
                    parcelle = get_parcelle_by_coords(click["lng"], click["lat"])
                    zone = get_zonage_geojson(click["lat"], click["lng"])

                    if st.session_state.multi_mode:
                        refs_existants = [p.ref_cadastrale for p in st.session_state.selected_parcelles]
                        if parcelle.ref_cadastrale not in refs_existants:
                            st.session_state.selected_parcelles.append(parcelle)
                            st.session_state.zones_geojson.append(zone)
                        # En multi-mode : garder le zoom et la position actuels
                    else:
                        st.session_state.selected_parcelles = [parcelle]
                        st.session_state.zones_geojson = [zone]
                        # Centrer sur la nouvelle parcelle au zoom max
                        st.session_state.map_center = _centroid(
                            parcelle.geometrie, fallback_lat=click["lat"], fallback_lon=click["lng"]
                        )
                        st.session_state.map_zoom = 19

                    st.session_state.map_version += 1
                    st.rerun()
                except Exception as e:
                    st.warning(f"Impossible d'identifier la parcelle : {e}")

    st.caption("Cliquez sur une parcelle pour la sélectionner. Activez le mode multi-parcelles pour en sélectionner plusieurs.")
