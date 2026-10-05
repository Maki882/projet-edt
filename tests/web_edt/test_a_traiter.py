"""Onglet « À traiter » : avec et sans vérificateur, liens vers les séances, séances à programmer."""

from __future__ import annotations

from sqlalchemy import select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import Gravite, StatutOccurrence
from edt.domaine.instantane import id_seance
from edt.verification.contrat import Anomalie

from .conftest import JOUR_TEST, identifiant_groupe, seance_du_besoin


def test_sans_emploi_du_temps_actif(jeu_minimal, client):
    page = client.get("/a-traiter").text
    assert "Aucun emploi du temps actif" in page


def test_sans_verificateur_l_absence_est_dite(version_generee, client, sans_verificateur):
    page = client.get("/a-traiter").text

    assert "Les vérifications automatiques ne sont pas encore disponibles" in page
    assert "Aucune anomalie" not in page
    assert client.get("/a-traiter/compteur").text.strip() == ""


def test_anomalies_par_gravite_et_contrainte_et_seances_a_programmer(
    seances_datees, client, faux_verificateur
):
    with session_base() as session:
        cm = seance_du_besoin(session, seances_datees.version_id, "Mathématiques", "CM", "A1")
        promotion = identifiant_groupe(session, "A1")
        occurrences = session.scalars(
            select(m.Occurrence).where(m.Occurrence.date == JOUR_TEST).order_by(m.Occurrence.id)
        ).all()
        en_conflit, a_programmer = occurrences[0], occurrences[1]
        a_programmer.statut = StatutOccurrence.A_PROGRAMMER
        a_programmer.commentaire = "Jour fermé et aucun autre créneau libre dans la semaine."
        ids = (cm.id, id_seance(cm.besoin_id, cm.rang), en_conflit.id, a_programmer.id)
    seance_hebdo_id, seance_id, conflit_id, a_programmer_id = ids
    faux_verificateur.hebdo = [
        Anomalie(
            "SALLE_UNIQUE",
            Gravite.ERREUR,
            "La salle N016 est prise deux fois.",
            seances=(seance_id,),
        ),
        Anomalie(
            "PAUSE", Gravite.AVERTISSEMENT, "Pas de pause pour la classe 1.", groupes=(promotion,)
        ),
        Anomalie(
            "HORAIRES", Gravite.INFORMATION, "DST imposé hors de la grille.", seances=(seance_id,)
        ),
    ]
    faux_verificateur.datees = [
        Anomalie(
            "JOUR_FERME",
            Gravite.ERREUR,
            "Séance un jour férié.",
            occurrences=(conflit_id,),
            date=JOUR_TEST,
        )
    ]

    page = client.get("/a-traiter").text

    assert page.index("Erreurs : règles non respectées") < page.index("Avertissements")
    assert page.index("Avertissements") < page.index("Informations")
    assert "Une séance par salle (1)" in page and "Jours fermés (1)" in page
    assert "La salle N016 est prise deux fois." in page
    assert f"seance=h{seance_hebdo_id}" in page and f"groupe_id={promotion}" in page
    assert f"couche=date&amp;date={JOUR_TEST.isoformat()}&amp;seance=o{conflit_id}" in page
    assert "Séances à programmer (1)" in page
    assert "Jour fermé et aucun autre créneau libre" in page
    assert f"seance=o{a_programmer_id}" in page
    # Erreurs et avertissements (3) + séance à programmer (1) ; les informations ne comptent pas.
    assert ">4</span>" in client.get("/a-traiter/compteur").text


def test_avec_le_vrai_verificateur(version_generee, client):
    from edt.services.emploi_du_temps import raccords

    with session_base() as session:
        verification = raccords.verifier_version(session, version_generee.version_id)

    page = client.get("/a-traiter").text

    assert "pas encore disponibles" not in page
    if verification.nb_erreurs:
        assert f"Erreurs : règles non respectées ({verification.nb_erreurs})" in page
    else:
        assert "Aucune anomalie dans la semaine type" in page
