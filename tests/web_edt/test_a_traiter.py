"""Onglet « À traiter » : avec et sans vérificateur, liens vers les séances, séances à programmer."""

from __future__ import annotations

from sqlalchemy import func, select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import Gravite, OrigineVersion, StatutOccurrence
from edt.domaine.instantane import id_seance
from edt.verification.contrat import Anomalie

from .conftest import date_avec_seances, identifiant_groupe, seance_du_besoin


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
        jour = date_avec_seances(session, seances_datees.version_id, minimum=2)
        deja = session.scalar(
            select(func.count())
            .select_from(m.Occurrence)
            .where(
                m.Occurrence.version_id == seances_datees.version_id,
                m.Occurrence.statut == StatutOccurrence.A_PROGRAMMER,
            )
        )
        occurrences = session.scalars(
            select(m.Occurrence)
            .where(
                m.Occurrence.version_id == seances_datees.version_id,
                m.Occurrence.date == jour,
                m.Occurrence.statut != StatutOccurrence.A_PROGRAMMER,
            )
            .order_by(m.Occurrence.id)
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
            date=jour,
        )
    ]

    page = client.get("/a-traiter").text

    assert page.index("Erreurs : règles non respectées") < page.index("Avertissements")
    assert page.index("Avertissements") < page.index("Informations")
    assert "Une séance par salle (1)" in page and "Jours fermés (1)" in page
    assert "La salle N016 est prise deux fois." in page
    assert f"seance=h{seance_hebdo_id}" in page and f"groupe_id={promotion}" in page
    assert f"couche=date&amp;date={jour.isoformat()}&amp;seance=o{conflit_id}" in page
    assert f"Séances à programmer ({deja + 1})" in page
    assert "Jour fermé et aucun autre créneau libre" in page
    assert f"seance=o{a_programmer_id}" in page
    # Erreurs et avertissements (3) + séances à programmer ; les informations ne comptent pas.
    assert f">{3 + deja + 1}</span>" in client.get("/a-traiter/compteur").text


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


def test_compteur_memorise_puis_recalcule_quand_une_seance_change(
    seances_datees, client, faux_verificateur, monkeypatch
):
    from edt.services.emploi_du_temps import anomalies as service_anomalies
    from edt.services.emploi_du_temps import compteur

    compteur.oublier()
    appels = []
    original = service_anomalies.a_traiter

    def a_traiter_compte(session, semestre_id=None):
        appels.append(semestre_id)
        return original(session, semestre_id)

    monkeypatch.setattr(service_anomalies, "a_traiter", a_traiter_compte)
    faux_verificateur.hebdo = [Anomalie("SALLE_UNIQUE", Gravite.ERREUR, "Salle prise deux fois.")]

    for _ in range(3):
        assert ">1</span>" in client.get("/a-traiter/compteur").text
    assert len(appels) == 1  # calculé une seule fois tant que rien ne change

    with session_base() as session:
        occurrence = session.scalar(select(m.Occurrence).limit(1))
        occurrence.statut = StatutOccurrence.A_PROGRAMMER  # une occurrence change
    assert ">2</span>" in client.get("/a-traiter/compteur").text
    assert len(appels) == 2

    with session_base() as session:
        seance = session.scalar(select(m.SeanceHebdo).limit(1))
        seance.verrou = not seance.verrou  # une séance change
    client.get("/a-traiter/compteur")
    assert len(appels) == 3


def test_option_tous_les_semestres_affiche_tous_les_semestres(version_generee, client):
    """L'option « Tous les semestres » envoie `semestre_id=` (vide) : jamais « Saisie invalide »."""
    with session_base() as session:
        autre = session.scalar(
            select(m.Semestre).where(m.Semestre.id != version_generee.semestre_id).limit(1)
        )
        assert autre is not None, "la base par défaut a deux semestres"
        session.add(
            m.VersionEdt(
                semestre_id=autre.id,
                numero=1,
                libelle="Version du second semestre",
                origine=OrigineVersion.REFERENCE,
                active=True,
            )
        )
        libelles = {s.id: s.libelle for s in session.scalars(select(m.Semestre)).all()}

    page = client.get("/a-traiter?semestre_id=")
    assert page.status_code == 200
    assert '<option value="" selected>Tous les semestres</option>' in page.text
    for libelle in libelles.values():  # les deux semestres ont une version active
        assert f"<h2>{libelle}" in page.text
    seul = client.get(f"/a-traiter?semestre_id={version_generee.semestre_id}").text
    assert f"<h2>{libelles[autre.id]}" not in seul

    # Même forme d'adresse sur Générer, l'historique et la grille : le semestre par défaut.
    for adresse in (
        "/calculs?semestre_id=",
        "/versions?semestre_id=",
        "/emploi-du-temps?semestre_id=",
    ):
        reponse = client.get(adresse)
        assert reponse.status_code == 200, adresse
        assert "Saisie invalide" not in reponse.text, adresse
