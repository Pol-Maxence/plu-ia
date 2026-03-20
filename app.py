"""
PLU·IA — Interface web Streamlit.

Lancement :
    streamlit run app.py
"""

import io
import tempfile

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="PLU·IA — Étude capacitaire",
    page_icon="🏗️",
    layout="centered",
)

st.title("PLU·IA")
st.caption("Étude capacitaire réglementaire automatisée")
st.divider()

# ---------------------------------------------------------------------------
# Mode de saisie
# ---------------------------------------------------------------------------

mode = st.radio(
    "Mode d'analyse",
    ["Adresse postale", "Référence cadastrale", "Multi-parcelles"],
    horizontal=True,
)

adresse = None
ref = None
refs = []

if mode == "Adresse postale":
    adresse = st.text_input(
        "Adresse",
        placeholder="15 rue de la Paix, Paris",
    )

elif mode == "Référence cadastrale":
    ref = st.text_input(
        "Référence cadastrale",
        placeholder="75056000BX0042",
    )

else:  # Multi-parcelles
    st.caption("Saisissez les références cadastrales des parcelles adjacentes à analyser ensemble.")
    n = st.number_input("Nombre de parcelles", min_value=2, max_value=10, value=2, step=1)
    for i in range(int(n)):
        r = st.text_input(f"Référence {i + 1}", placeholder="75056000BX0042", key=f"ref_{i}")
        refs.append(r)

output_path = "output/rapport.pdf"

# ---------------------------------------------------------------------------
# Lancement de l'analyse
# ---------------------------------------------------------------------------

if st.button("Analyser", type="primary", use_container_width=True):
    # Validation des entrées
    if mode == "Adresse postale" and not adresse:
        st.error("Veuillez saisir une adresse.")
        st.stop()
    elif mode == "Référence cadastrale" and not ref:
        st.error("Veuillez saisir une référence cadastrale.")
        st.stop()
    elif mode == "Multi-parcelles" and any(not r.strip() for r in refs):
        st.error("Veuillez remplir toutes les références cadastrales.")
        st.stop()

    # Fichier PDF temporaire
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        with st.status("Analyse en cours…", expanded=True) as status:

            if mode == "Multi-parcelles":
                from src.main import run_multi
                refs_clean = [r.strip() for r in refs]

                st.write(f"Récupération de {len(refs_clean)} parcelle(s)…")
                # run_multi gère toutes les étapes
                st.write("Récupération zonage PLU…")
                st.write("Analyse PLU par IA…")
                st.write("Calcul capacitaire…")
                st.write("Génération du rapport PDF…")
                run_multi(refs=refs_clean, output=tmp_path)

            else:
                from src.main import run

                st.write("Récupération de la parcelle…")
                st.write("Récupération zonage PLU…")
                st.write("Analyse PLU par IA…")
                st.write("Calcul capacitaire…")
                st.write("Génération du rapport PDF…")
                run(
                    adresse=adresse if mode == "Adresse postale" else None,
                    ref_cadastrale=ref if mode == "Référence cadastrale" else None,
                    output=tmp_path,
                )

            status.update(label="Analyse terminée ✓", state="complete")

        # Lecture du PDF généré
        with open(tmp_path, "rb") as f:
            pdf_bytes = f.read()

        st.success("Rapport généré avec succès.")
        st.download_button(
            label="⬇ Télécharger le rapport PDF",
            data=pdf_bytes,
            file_name="rapport_plu_ia.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    except Exception as e:
        st.error(f"Erreur : {e}")
