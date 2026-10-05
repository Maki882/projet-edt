"""Aucune page de l'espace « Données » ne plante ni n'affiche de trace technique."""

from __future__ import annotations

from edt.web.routes import donnees_besoins, donnees_etablissement, donnees_jeux, donnees_regles

ECRANS = (
    *donnees_etablissement.ECRANS,
    donnees_besoins.ECRAN_BESOINS,
    donnees_besoins.ECRAN_INDISPONIBILITES,
    donnees_regles.ECRAN_ANNEES,
    donnees_regles.ECRAN_SEMESTRES,
    donnees_regles.ECRAN_JOURS_FERMES,
    donnees_regles.ECRAN_GRILLE,
)


def _adresses() -> list[str]:
    adresses = [entree.url for module in (donnees_regles, donnees_jeux) for entree in module.MENU]
    adresses += ["/groupes/arbre"]
    for ecran in ECRANS:
        adresses += [
            ecran.prefixe,
            f"{ecran.prefixe}/nouveau",
            f"{ecran.prefixe}/1",
            f"{ecran.prefixe}/1/supprimer",
            f"{ecran.prefixe}?q=%25_&tri={ecran.entite.tri_defaut}&sens=desc&page=1",
        ]
    return adresses


def test_toutes_les_pages_repondent_sans_trace(site):
    for adresse in _adresses():
        reponse = site.get(adresse)
        assert reponse.status_code == 200, adresse
        assert "Traceback" not in reponse.text and "Une erreur inattendue" not in reponse.text, (
            adresse
        )


def test_adresses_erronees_expliquees(site):
    for adresse, statut in (
        ("/salles/99999", 404),
        ("/salles/99999/supprimer", 404),
        ("/salles?tri=inconnu", 422),
        ("/besoins?semestre=abc", 422),
        ("/groupes/arbre?annee=999", 404),
    ):
        reponse = site.get(adresse)
        assert reponse.status_code == statut, adresse
        assert "Traceback" not in reponse.text, adresse
