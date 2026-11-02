"""Constats de la revue d'ensemble (lot web-edt, n° 20 à 28) et mémoire des écrans."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, func, select, update

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite, OrigineOccurrence, StatutOccurrence
from edt.erreurs import ErreurRegleMetier
from edt.services.emploi_du_temps import (
    anomalies,
    compteur,
    memoire,
    modifications,
    raccords,
    tableau_de_bord,
)
from edt.verification.contrat import Anomalie

from .conftest import HX, autre_place, seance_du_besoin


def _nb_occurrences(version_id: int) -> int:
    with session_base() as session:
        return session.scalar(
            select(func.count())
            .select_from(m.Occurrence)
            .where(m.Occurrence.version_id == version_id)
        )


def _importer_les_seances_datees(version_id: int) -> None:
    """Rend les séances datées de la version semblables à un import « référence »."""
    with session_base() as session:
        session.execute(
            update(m.Occurrence)
            .where(m.Occurrence.version_id == version_id)
            .values(origine=OrigineOccurrence.IMPORT, seance_hebdo_id=None)
        )


# --------------------------------------------------------------------------- #
# n° 20 : séances datées importées
# --------------------------------------------------------------------------- #


def test_seances_datees_importees_jamais_recalculees(seances_datees, client, faux_verificateur):
    version = seances_datees.version_id
    _importer_les_seances_datees(version)
    avant = _nb_occurrences(version)

    with session_base() as session, pytest.raises(ErreurRegleMetier, match="importées"):
        modifications.recalculer_seances_datees(session, version)

    adresse = f"/emploi-du-temps?version_id={version}&couche=date"
    page = client.get(adresse).text
    assert "Recalculer les séances datées" not in page  # bouton masqué
    assert "ont été importées" in page
    refus = client.post(
        f"/emploi-du-temps/versions/{version}/seances-datees", data={"retour": adresse}
    )
    assert refus.status_code == 200 and "les doublerait" in refus.text

    # Déplacer une séance de la semaine type reste possible, sans toucher aux séances datées.
    with session_base() as session:
        seance = seance_du_besoin(session, version, "Mathématiques", "CM", "A1")
        resultat = modifications.modifier_placement(
            session, seance.id, jour=seance.jour, debut=seance.debut_min, salle_id=None
        )
        assert resultat.enregistree
        assert any("n'ont pas été recalculées" in remarque for remarque in resultat.remarques)
    assert _nb_occurrences(version) == avant


def test_un_refus_de_l_instanciation_devient_une_remarque(seances_datees, client, monkeypatch):
    def refuser(session, version_id):
        raise ErreurRegleMetier("L'instanciation refuse cette version.")

    monkeypatch.setattr(raccords, "instancier_version", refuser)
    version = seances_datees.version_id
    with session_base() as session:
        assert modifications._recalculer_seances_datees(session, version) == [
            "Les séances datées de cette version n'ont pas été recalculées : l'instanciation "
            "refuse cette version."
        ]
    reponse = client.post(
        f"/emploi-du-temps/versions/{version}/seances-datees",
        data={"retour": f"/emploi-du-temps?version_id={version}&couche=date"},
    )
    assert reponse.status_code == 200 and "refuse cette version" in reponse.text


# --------------------------------------------------------------------------- #
# n° 21 et 22 : un seul nombre de points à traiter
# --------------------------------------------------------------------------- #


def _trois_nombres(client) -> tuple[int, int, int]:
    """Points à traiter selon l'accueil, la pastille de l'onglet et la page « À traiter »."""
    with session_base() as session:
        semestre = session.scalar(select(m.Semestre).where(m.Semestre.code == "S1"))
        from edt.services.emploi_du_temps.versions import exiger_semestre

        resume = exiger_semestre(session, semestre.id)
        accueil = tableau_de_bord.tableau_de_bord(session, semestre.date_debut).nb_a_traiter
        pastille = compteur.compteur_a_traiter(session).total
        page = sum(element.total for element in anomalies.a_traiter(session, resume.id))
    return accueil, pastille, page


def test_meme_nombre_de_points_partout_et_seance_a_programmer_comptee_une_fois(
    seances_datees, client, aujourdhui_fige
):
    version = seances_datees.version_id
    depart = _trois_nombres(client)
    assert depart[0] == depart[1] == depart[2]

    with session_base() as session:
        occurrence = session.scalar(
            select(m.Occurrence)
            .where(
                m.Occurrence.version_id == version,
                m.Occurrence.statut == StatutOccurrence.PLANIFIEE,
            )
            .limit(1)
        )
        occurrence.statut = StatutOccurrence.A_PROGRAMMER
    apres = _trois_nombres(client)
    assert apres[0] == apres[1] == apres[2] == depart[0] + 1  # une fois, pas deux

    # Une indisponibilité datée d'un enseignant réel pendant l'un de ses cours, lu dans la
    # version (la solution change d'un système à l'autre) : erreurs datées seulement.
    with session_base() as session:
        trouve = session.execute(
            select(m.Occurrence.date, m.besoin_enseignant.c.enseignant_id)
            .join(m.besoin_enseignant, m.besoin_enseignant.c.besoin_id == m.Occurrence.besoin_id)
            .join(m.Enseignant, m.Enseignant.id == m.besoin_enseignant.c.enseignant_id)
            .where(
                m.Occurrence.version_id == version,
                # pas la séance passée « à programmer » : elle n'a plus lieu ce jour-là
                m.Occurrence.statut.in_([StatutOccurrence.PLANIFIEE, StatutOccurrence.DEPLACEE]),
                m.Enseignant.fictif.is_(False),
            )
            .order_by(m.Occurrence.date, m.Occurrence.debut_min, m.Occurrence.id)
            .limit(1)
        ).first()
        assert trouve is not None, "aucune séance datée qui a lieu avec un enseignant réel"
        jour, enseignant = trouve
        session.add(
            m.Indisponibilite(
                cible="enseignant",
                enseignant_id=enseignant,
                date_debut=jour,
                date_fin=jour,
                motif="Arrêt maladie",
            )
        )
    final = _trois_nombres(client)
    assert final[0] == final[1] == final[2] > apres[0]
    page = client.get("/").text
    assert f"{final[0]} points à traiter" in page or f"{final[0]} point à traiter" in page


# --------------------------------------------------------------------------- #
# n° 23 : contraintes décochées lors du calcul
# --------------------------------------------------------------------------- #


def test_contraintes_decochees_tolerees_par_la_retouche_et_mises_a_part(
    version_generee, client, faux_verificateur
):
    version = version_generee.version_id
    with session_base() as session:
        calcul = session.get(m.Calcul, version_generee.calcul_id)
        calcul.contraintes_desactivees = ["CAPACITE"]
        seance = seance_du_besoin(session, version, "Mathématiques", "CM", "A1")
        identifiant, jour, debut, salle = seance.id, seance.jour, seance.debut_min, seance.salle_id
        autre_jour, autre_debut = autre_place(session, seance)  # tirée de la version
    capacite = Anomalie(CodeContrainte.CAPACITE, Gravite.ERREUR, "La salle est trop petite.")
    faux_verificateur.modification = [capacite]
    faux_verificateur.hebdo = [capacite]

    with session_base() as session:
        resultat = modifications.modifier_placement(
            session, identifiant, jour=autre_jour, debut=autre_debut, salle_id=salle
        )
    assert resultat.enregistree  # la règle décochée au calcul ne bloque pas la retouche
    # Aucune liste en dur : le vérificateur applique les niveaux du calcul de la version.
    assert faux_verificateur.contraintes_modification is None

    with session_base() as session:
        controle = raccords.controler_version(session, version)
        assert controle.verification.nb_erreurs == 0 and controle.nb_ecarts_decoches == 1
    page = client.get("/a-traiter").text
    assert "Écarts aux règles décochées lors du calcul (1)" in page
    assert "0 point à traiter" in page
    accueil = client.get("/").text
    assert "Aucun point à traiter" in accueil  # même nombre que la page « À traiter »

    # Une règle active reste bloquante.
    faux_verificateur.modification = [
        Anomalie(CodeContrainte.SALLE_UNIQUE, Gravite.ERREUR, "La salle est déjà prise.")
    ]
    with session_base() as session:
        refus = modifications.modifier_placement(
            session,
            identifiant,
            jour=jour,
            debut=debut,
            salle_id=salle,  # retour à sa place
        )
    assert not refus.enregistree


# --------------------------------------------------------------------------- #
# n° 24 à 28
# --------------------------------------------------------------------------- #


def test_calcul_dont_la_version_a_ete_supprimee(version_generee, client):
    with session_base() as session:
        session.execute(delete(m.VersionEdt).where(m.VersionEdt.id == version_generee.version_id))
    page = client.get(f"/calculs/{version_generee.calcul_id}").text
    assert "version_id=None" not in page
    assert "a depuis été supprimée" in page


def test_date_extreme_sans_erreur(version_generee, client):
    reponse = client.get(
        f"/emploi-du-temps?version_id={version_generee.version_id}&couche=date&date=9999-12-31"
    )
    assert reponse.status_code == 200
    assert "trop loin" in reponse.text


def test_pas_de_calcul_sans_besoin(base, client):
    with session_base() as session:
        semestre = session.scalar(select(m.Semestre.id).where(m.Semestre.code == "S1"))
    page = client.get(f"/calculs?semestre_id={semestre}").text
    assert page.count("aucun besoin d&#39;enseignement") == 1
    assert "disabled" in page
    refus = client.post(
        "/calculs",
        data={"semestre_id": semestre, "contraintes_envoyees": "1"},
        follow_redirects=False,
    )
    assert refus.status_code == 409
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(m.Calcul)) == 0


def test_accueil_sans_details_techniques_en_production(client, monkeypatch):
    page = client.get("/").text
    assert 'href="/jeux"' in page and "python -m edt jeu-minimal" not in page
    monkeypatch.setenv("EDT_ENV", "prod")
    production = client.get("/").text
    assert "Emplacement" not in production and "sqlite:///" not in production
    assert "mode wal" not in production


def test_meme_nom_de_version_partout(version_generee, client):
    with session_base() as session:
        libelle = session.get(m.VersionEdt, version_generee.version_id).libelle
    page = client.get(f"/emploi-du-temps?version_id={version_generee.version_id}").text
    assert f">{libelle} (active)</option>" in page.replace("&#39;", "'")


# --------------------------------------------------------------------------- #
# Mémoire des écrans
# --------------------------------------------------------------------------- #


def test_verifications_memorisees_jusqu_a_la_prochaine_ecriture(
    version_generee, client, faux_verificateur
):
    version = version_generee.version_id
    for _ in range(3):
        assert client.get(f"/emploi-du-temps?version_id={version}").status_code == 200
    semaine_type = [a for a in faux_verificateur.appels if a[0] == "version"]
    assert len(semaine_type) == 1  # trois affichages, une seule vérification

    with session_base() as session:
        seance = session.scalar(
            select(m.SeanceHebdo).where(m.SeanceHebdo.version_id == version).limit(1)
        )
        seance.verrou = not seance.verrou  # une écriture : la mémoire est périmée
    client.get(f"/emploi-du-temps?version_id={version}")
    assert len([a for a in faux_verificateur.appels if a[0] == "version"]) == 2
    memoire.oublier()


def test_prechauffage_remplit_la_memoire_des_ecrans(version_generee, client, faux_verificateur):
    from edt.config import charger_configuration
    from edt.services.emploi_du_temps import prechauffage

    prechauffage._prechauffer(charger_configuration().db_url)  # le corps du fil, sans le fil
    verifications = len(faux_verificateur.appels)
    assert verifications >= 2  # semaine type et séances datées de la version active

    for adresse in ("/", "/a-traiter", "/a-traiter/compteur"):
        assert client.get(adresse).status_code == 200
    assert len(faux_verificateur.appels) == verifications  # tout venait de la mémoire


def test_besoin_impossible_a_deplier_affiche_une_seule_fois(jeu_minimal, client):
    import datetime as dt

    with session_base() as session:
        besoin = session.scalar(
            select(m.Besoin).where(m.Besoin.date_ponctuelle.is_not(None)).order_by(m.Besoin.id)
        )
        besoin.date_ponctuelle = dt.date(2027, 3, 1)  # hors du semestre 1
    page = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text

    assert page.count("01/03/2027") == 1
    assert "Le calcul ne peut pas être lancé" in page


def test_refus_reel_de_l_instanciation_sans_erreur_500(
    seances_datees, client, faux_verificateur, monkeypatch
):
    """L'instanciation livrée refuse une version importée : ni le recalcul ni un déplacement ne plantent."""
    version = seances_datees.version_id
    _importer_les_seances_datees(version)
    # Sans le garde de l'écran, c'est `instancier_version` elle-même qui refuse.
    monkeypatch.setattr(modifications, "seances_datees_importees", lambda session, v: False)
    avant = _nb_occurrences(version)

    adresse = f"/emploi-du-temps?version_id={version}&couche=date"
    recalcul = client.post(
        f"/emploi-du-temps/versions/{version}/seances-datees", data={"retour": adresse}
    )
    assert recalcul.status_code == 200 and "les doublerait" in recalcul.text
    assert "Traceback" not in recalcul.text

    with session_base() as session:
        seance = seance_du_besoin(session, version, "Mathématiques", "CM", "A1")
        identifiant, salle = seance.id, seance.salle_id
        jour, debut = autre_place(session, seance)
    deplacement = client.post(
        f"/emploi-du-temps/seances/{identifiant}/modifier",
        data={"jour": jour, "debut": debut, "salle_id": str(salle), "retour": adresse},
        headers=HX,
    )
    assert deplacement.status_code == 204  # enregistré malgré le refus
    assert "n&#39;ont pas été recalculées" in client.get(adresse).text  # dit, pas avalé
    assert _nb_occurrences(version) == avant
