"""Résultat d'un calcul : les indicateurs de qualité affichés concordent avec le vérificateur.

Le calcul est complet (vrai moteur, jeu minimal). Les valeurs attendues sont relues dans le
rapport du vérificateur pour la même version, jamais écrites en dur : elles changent avec la
solution du moteur, qui diffère d'un système à l'autre.
"""

from __future__ import annotations

import html
import re

from edt.db.moteur import session_base
from edt.services import verification
from edt.services.emploi_du_temps.presentation import pourcentage


def _compteur(page: str, libelle: str) -> str:
    """Le nombre affiché devant le libellé d'un compteur de la page."""
    trouve = re.search(
        r'<span class="compteur-nombre">([^<]*)</span>\s*'
        rf'<span class="compteur-libelle">{re.escape(libelle)}',
        page,
    )
    assert trouve is not None, libelle
    return trouve.group(1).strip()


def test_resultat_d_un_calcul_affiche_les_indicateurs_du_verificateur(version_generee, client):
    """Après un calcul complet, l'utilisateur lit sur la page du résultat la part des séances
    placées, le nombre de conflits, l'utilisation des salles et les changements de campus :
    ce sont les valeurs que le vérificateur rend pour la version produite."""
    page = html.unescape(client.get(f"/calculs/{version_generee.calcul_id}").text)
    assert "Qualité de l'emploi du temps" in page

    with session_base() as session:
        rapport = verification.verifier_version(session, version_generee.version_id)
    indicateurs = rapport.indicateurs
    assert indicateurs is not None
    changements = (
        indicateurs.changements_campus_groupes + indicateurs.changements_campus_enseignants
    )

    assert _compteur(page, "des séances placées") == pourcentage(
        indicateurs.pourcentage_seances_placees
    )
    assert _compteur(page, "des séances placées") == "100 %"  # calcul réussi : tout est placé
    assert _compteur(page, "conflit") == str(rapport.nb_erreurs)
    assert re.search(r"conflits? \(règles non respectées\)", page)
    assert _compteur(page, "d'utilisation des salles") == pourcentage(
        indicateurs.pourcentage_utilisation_salles
    )
    libelle_campus = "changement de campus" if changements < 2 else "changements de campus"
    assert _compteur(page, libelle_campus) == str(changements)
