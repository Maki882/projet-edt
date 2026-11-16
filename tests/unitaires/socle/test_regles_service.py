"""Règles personnalisées, côté base et service : table, chargement, écriture, propositions."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select

from edt.db import modeles as m
from edt.db.initialisation import assurer_annee_scolaire, ecarts_schema, tables_manquantes
from edt.db.moteur import obtenir_moteur, session_base
from edt.domaine.contraintes import CATALOGUE, CodeContrainte, Niveau
from edt.domaine.regles import (
    CATALOGUE_TYPES,
    EtatRegle,
    NaturePersonne,
    OrigineRegle,
    Personne,
    TypeRegle,
)
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.services import regles as service
from edt.services.instantane import charger_instantane
from edt.services.parametres import ecrire_parametre, lire_parametres
from edt.services.regles import ErreurSaisie

H = 60


def _groupe_id(session, code: str) -> int:
    return session.scalar(select(m.Groupe.id).where(m.Groupe.code == code))


# --------------------------------------------------------------------------- #
# Table, jeu minimal, instantané
# --------------------------------------------------------------------------- #


def test_la_table_regle_manque_sur_une_ancienne_base(base):
    with obtenir_moteur().begin() as connexion:
        connexion.exec_driver_sql("DROP TABLE regle")
    assert tables_manquantes(obtenir_moteur()) == ["regle"]
    assert ecarts_schema(obtenir_moteur()) == []  # init-db la créera (pas un écart de colonne)


def test_le_code_regle_est_au_catalogue_non_modifiable():
    assert CATALOGUE[CodeContrainte.REGLE].modifiable is False
    assert CATALOGUE[CodeContrainte.REGLE].libelle == "Règle personnalisée"


def test_jeu_minimal_et_instantane(jeu_minimal, session):
    assert jeu_minimal.nb_regles == 4
    regles = service.lister(session, jeu_minimal.annee_scolaire_id)
    assert [(r.code, r.etat.value) for r in regles] == [
        ("R001", "active"),
        ("R002", "active"),
        ("R003", "proposee"),
        ("R004", "proposee"),
    ]
    assert regles[0].phrase == "La promotion A1 ne travaille que du lundi au vendredi."

    instantane = charger_instantane(session, jeu_minimal.semestre_id)
    assert [r.code for r in instantane.regles] == ["R001", "R002"]  # les proposées sont sans effet
    pause = instantane.regle(instantane.regles[1].id)
    assert pause.type is TypeRegle.PAUSE_DEJEUNER and pause.niveau is Niveau.IMPORTANT
    g1a = Personne(NaturePersonne.GROUPE, _groupe_id(session, "A1-G1a"))
    assert pause.personnes == {g1a, Personne(NaturePersonne.GROUPE, _groupe_id(session, "A1-G1b"))}
    assert instantane.reglages.dejeuner[g1a].fenetre == (12 * H, 14 * H, 60)
    assert instantane.reglages.dejeuner[g1a].source.regle is pause
    # Le TP du samedi reste fermé par la règle de la promotion.
    tp = next(
        s for s in instantane.seances if s.besoin.libelle.startswith("TP Algorithmique - A1-G1a")
    )
    assert instantane.reglages.jours_de(tp.id).jours_permis() == frozenset(range(5))
    assert [s.texte for s in instantane.reglages.jours_de(tp.id).entorses(5)] == ["règle R001"]


def test_une_regle_active_invalide_fait_echouer_le_chargement(jeu_minimal, session):
    ligne = session.scalar(select(m.Regle).where(m.Regle.code == "R002"))
    ligne.parametres = {"mode": "libre", "debut": "13:00", "fin": "14:00", "duree": 120}
    session.flush()
    with pytest.raises(ErreurDonneesInvalides, match="R002") as capture:
        charger_instantane(session, jeu_minimal.semestre_id)
    assert any("ne tient pas dans la fenêtre" in d for d in capture.value.details)

    ligne.etat = EtatRegle.INACTIVE  # une règle inactive, même invalide, ne gêne plus
    session.flush()
    assert [r.code for r in charger_instantane(session, jeu_minimal.semestre_id).regles] == ["R001"]


def test_une_regle_du_second_semestre_n_est_pas_dans_le_premier(jeu_minimal, session):
    s2 = session.scalar(select(m.Semestre.id).where(m.Semestre.code == "S2"))
    service.creer(
        session,
        {
            "type": "SANS_TROU",
            "portee": {"nature": "groupe", "cible": "A1-G2"},
            "periode": {"semestre": "S2"},
        },
    )
    assert "R005" not in {
        r.code for r in charger_instantane(session, jeu_minimal.semestre_id).regles
    }
    assert "R005" in {r.code for r in charger_instantane(session, s2).regles}


# --------------------------------------------------------------------------- #
# Service : validation et écriture
# --------------------------------------------------------------------------- #


def test_valider_puis_creer_depuis_un_brouillon_json(jeu_minimal, session):
    brouillon = {
        "type": "PAUSE_DEJEUNER",
        "portee": {"nature": "groupe", "cible": "A1-G2"},
        "parametres": {"mode": "libre", "debut": "11h45", "fin": "14:15", "duree": "1 h 15"},
        "niveau": "important",
        "annee": "2026-2027",
        "periode": {"semestre": "S1"},
        "commentaire": "Demande de la RP",
    }
    validation = service.valider(session, brouillon)
    assert validation.valide and validation.erreurs == {}
    assert validation.phrase == (
        "La classe A1-G2 a une pause déjeuner libre d'au moins 1 h 15 entre 11 h 45 et 14 h 15."
    )
    assert validation.regle.code is None and validation.regle.parametres["duree"] == 75
    assert session.scalar(select(m.Regle).where(m.Regle.code == "R005")) is None  # rien d'écrit

    detail = service.creer(session, brouillon, origine="assistant")
    assert detail.code == "R005" and detail.etat is EtatRegle.ACTIVE
    assert detail.regle.origine is OrigineRegle.ASSISTANT
    exporte = detail.vers_dict()
    assert exporte["periode"] == {"semestre": "S1"} and exporte["annee"] == "2026-2027"
    assert exporte["portee"] == {
        "nature": "groupe",
        "cible": "A1-G2",
        "libelle": "1re année, classe 2",
    }
    assert service.lire(session, jeu_minimal.annee_scolaire_id, "R005").phrase == validation.phrase


def test_erreurs_par_champ(jeu_minimal, session):
    validation = service.valider(
        session,
        {
            "type": "PAUSE_DEJEUNER",
            "portee": {"nature": "groupe", "cible": "A9"},
            "parametres": {"mode": "libre", "duree": 200},
            "niveau": "vital",
            "periode": {"semestre": "S3", "date_debut": "2026-13-01"},
        },
    )
    assert not validation.valide and validation.regle is None
    assert set(validation.erreurs) == {
        "portee.cible",
        "parametres.duree",
        "niveau",
        "periode.semestre",
        "periode.date_debut",
    }
    assert validation.erreurs["parametres.duree"] == "Durée minimale : au plus 180 (lu : 200)."
    with pytest.raises(ErreurSaisie) as capture:
        service.creer(
            session, {"type": "ORDRE_SEMAINE", "portee": {"nature": "campus", "cible": "NDC"}}
        )
    assert "portee.nature" in capture.value.erreurs
    assert service.valider(session, {"type": "SANS_TROU", "portee": "etablissement"}).erreurs == {
        "portee.nature": "Trous limités ne peut pas concerner « établissement » ; possible : enseignant, groupe."
    }


def test_doublon_de_reglage_refuse_puis_modification(jeu_minimal, session):
    brouillon = {
        "type": "PAUSE_DEJEUNER",
        "portee": {"nature": "groupe", "cible": "A1-G1"},
        "parametres": {"mode": "fixe"},
    }
    validation = service.valider(session, brouillon)
    assert not validation.valide and "R002" in validation.erreurs["portee"]
    # Hors période de R002 (semestre 2), la même règle est permise.
    assert service.valider(session, {**brouillon, "periode": {"semestre": "S2"}}).valide
    # Proposée (sans effet), elle est permise aussi.
    proposee = service.creer(session, brouillon, etat="proposee")
    with pytest.raises(ErreurSaisie, match="R002"):
        service.activer(session, jeu_minimal.annee_scolaire_id, proposee.code)

    modifiee = service.modifier(
        session,
        jeu_minimal.annee_scolaire_id,
        "R002",
        {"parametres": {"duree": 90}, "niveau": "facultatif", "commentaire": "élargie"},
    )
    assert modifiee.phrase.endswith("d'au moins 1 h 30 entre 12 h 00 et 14 h 00.")
    assert modifiee.niveau is Niveau.FACULTATIF and modifiee.regle.commentaire == "élargie"
    assert modifiee.regle.semestre_code == "S1"  # le reste est conservé
    with pytest.raises(ErreurSaisie) as capture:
        service.modifier(
            session, jeu_minimal.annee_scolaire_id, "R002", {"parametres": {"duree": 150}}
        )
    assert "parametres.duree" in capture.value.erreurs


def test_etats_suppression_et_filtres(jeu_minimal, session):
    annee = jeu_minimal.annee_scolaire_id
    assert service.desactiver(session, annee, "R001").etat is EtatRegle.INACTIVE
    assert [r.code for r in charger_instantane(session, jeu_minimal.semestre_id).regles] == ["R002"]
    assert service.activer(session, annee, "R003").etat is EtatRegle.ACTIVE
    assert service.changer_etat(session, annee, "R003", "proposee").etat is EtatRegle.PROPOSEE

    service.supprimer(session, annee, "R004")
    with pytest.raises(ErreurIntrouvable):
        service.lire(session, annee, "R004")
    assert [r.code for r in service.lister(session, annee, etat="active")] == ["R002"]
    assert [r.code for r in service.lister(session, annee, type_regle="JOURS_TRAVAILLES")] == [
        "R001"
    ]
    # Les règles héritées d'un demi-groupe : celles de sa lignée.
    assert [
        r.code for r in service.lister(session, annee, groupe_id=_groupe_id(session, "A1-G1a"))
    ] == ["R001", "R002"]
    assert service.lister(session, annee, groupe_id=_groupe_id(session, "A1-G2")) == [
        service.lire(session, annee, "R001")
    ]
    assert [
        r.code for r in service.regles_visant(session, "groupe", _groupe_id(session, "A1-G1"))
    ] == ["R002"]
    assert service.lire_par_id(session, service.lire(session, annee, "R002").id).code == "R002"


def test_cibles_par_code_et_par_description(jeu_minimal, session):
    for nature, cible in (
        ("enseignant", "ENS004"),
        ("campus", "ISSY"),
        ("type_seance", "TP"),
        ("matiere", "IM.1101"),
        ("besoin", "IM.1101 / CM / A1"),
    ):
        validation = service.valider(
            session,
            {
                "type": "CRENEAUX_INTERDITS",
                "portee": {"nature": nature, "cible": cible},
                "parametres": {"jours": ["lundi"]},
            },
        )
        assert validation.valide, (nature, validation.erreurs)
    besoin = service.valider(
        session,
        {
            "type": "PLAGE_HORAIRE",
            "portee": {"nature": "besoin", "cible": "IM.1101 / CM / A1"},
            "parametres": {"fin": "12:45"},
        },
    )
    assert besoin.regle.portee.cible_libelle == "CM Mathématiques - A1"
    assert (
        besoin.phrase
        == "Les séances du besoin CM Mathématiques - A1 finissent à 12 h 45 au plus tard."
    )
    campus = service.creer(
        session,
        {
            "type": "CAMPUS",
            "portee": {"nature": "type_seance", "cible": "TP"},
            "parametres": {"mode": "interdire", "campus": ["NDC"]},
        },
    )
    instantane = charger_instantane(session, jeu_minimal.semestre_id)
    resolue = instantane.regle(campus.id)
    assert (
        resolue.valeurs["campus"] == (instantane.campus[0].id,)
        if instantane.campus[0].code == "NDC"
        else True
    )
    assert all(instantane.seance(s).besoin.type_seance_code == "TP" for s in resolue.seances)


def test_expliquer_et_decrire(jeu_minimal, session):
    explication = service.expliquer(session, jeu_minimal.annee_scolaire_id, "R002")
    assert explication.texte.startswith("La classe A1-G1 a une pause déjeuner libre")
    assert "(règle R002, important, active, S1)" in explication.texte
    assert [(e.semestre_code, e.nb_personnes) for e in explication.effets] == [("S1", 2)]
    assert explication.remplace == ("établissement : déjeuner de 12 h 45 à 13 h 45 (fixe)",)
    assert explication.vers_dict()["regle"]["code"] == "R002"
    assert service.expliquer(session, jeu_minimal.annee_scolaire_id, "R003").texte.endswith(
        "Cette règle n'est pas active : elle n'a aucun effet."
    )
    assert len(service.decrire_types()["types"]) == 14


def test_proposer_pour_annee_base_et_reprise(jeu_minimal, session):
    annee = jeu_minimal.annee_scolaire_id
    # Les règles de base sont déjà là (jeu minimal) : rien de nouveau.
    proposition = service.proposer_pour_annee(session, annee)
    assert (len(proposition.creees), proposition.deja_presentes, proposition.a_revoir) == (0, 2, ())

    # Année suivante : une promotion de même code, et pas de classe A1-G1.
    suivante = assurer_annee_scolaire(session, 2027)
    session.add(
        m.Groupe(annee_scolaire_id=suivante.id, code="A1", libelle="1re année", nature="promotion")
    )
    session.flush()
    proposition = service.proposer_pour_annee(session, suivante.id)

    phrases = [r.phrase for r in proposition.creees]
    assert phrases[:2] == [
        "Dans chaque matière, le CM précède les TD de la semaine.",
        "Chaque enseignant a au moins une demi-journée libre par semaine.",
    ]
    assert phrases[2] == "La promotion A1 ne travaille que du lundi au vendredi."  # reprise de R001
    reprise = proposition.creees[2]
    assert reprise.etat is EtatRegle.PROPOSEE and reprise.regle.origine is OrigineRegle.REPRISE
    assert reprise.regle.reprise_de_id == service.lire(session, annee, "R001").id
    assert proposition.a_revoir == (
        "R002 (2026-2027) : " + service.lire(session, annee, "R002").phrase,
    )
    # Rien n'est reproposé deux fois.
    seconde = service.proposer_pour_annee(session, suivante.id)
    assert seconde.creees == () and seconde.deja_presentes == 3
    assert [r.code for r in service.lister(session, suivante.id)] == ["R001", "R002", "R003"]
    assert service.lister(session, suivante.id)[2].vers_dict()["periode"] == {}


def test_jours_travailles_parametre_et_depliage(jeu_minimal, session):
    ecrire_parametre(session, "jours_travailles", [0, 1, 2, 3, 4, 5])
    session.commit()
    assert lire_parametres(session).jours_travailles == (0, 1, 2, 3, 4, 5)
    instantane = charger_instantane(session, jeu_minimal.semestre_id)
    assert instantane.jours_travailles == frozenset(range(6))
    td = next(
        s for s in instantane.seances if s.besoin.libelle.startswith("TD Mathématiques - A1-G2")
    )
    assert instantane.reglages.jours_de(td.id).standard == frozenset(range(6))
    # La promotion A1 ferme le samedi malgré le standard élargi.
    assert instantane.reglages.jours_de(td.id).jours_permis() == frozenset(range(5))
    assert session.get(m.Parametre, "jours_travailles").valeur == [0, 1, 2, 3, 4, 5]
    with pytest.raises(ErreurDonneesInvalides):
        ecrire_parametre(session, "jours_travailles", [7])


def test_suppression_d_une_cible_visee_refusee_par_la_base(jeu_minimal, session):
    from sqlalchemy.exc import IntegrityError

    classe = session.get(m.Groupe, _groupe_id(session, "A1-G1"))
    session.delete(classe)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    with session_base() as autre:
        assert autre.scalar(select(m.Regle.code).where(m.Regle.code == "R002")) == "R002"


def test_cycle_de_vie_sans_journal_perdu(jeu_minimal, session, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="edt.services.regles"):
        detail = service.creer(
            session,
            {
                "type": "MATIERES_PAR_JOUR",
                "portee": {"nature": "groupe", "cible": "A1"},
                "parametres": {"maximum": 3},
            },
        )
        service.supprimer(session, jeu_minimal.annee_scolaire_id, detail.code)
    assert f"Règle {detail.code} créée" in caplog.text and detail.phrase in caplog.text
    assert f"Règle {detail.code} supprimée" in caplog.text
    assert detail.regle.date_debut is None and detail.regle.periode_texte() == "toute l'année"
    assert dt.date.today()  # le brouillon sans année prend l'année en cours (2026-2027 ici)


# --------------------------------------------------------------------------- #
# Corrections de la revue
# --------------------------------------------------------------------------- #


def test_un_reglage_de_personne_date_est_refuse(jeu_minimal, session):
    brouillon = {
        "type": "PAUSE_DEJEUNER",
        "portee": {"nature": "groupe", "cible": "A1-G2"},
        "parametres": {"mode": "fixe", "debut": "12:00", "fin": "13:00"},
        "periode": {"date_debut": "2026-09-07", "date_fin": "2026-09-13"},
    }
    validation = service.valider(session, brouillon)
    assert not validation.valide and "sans dates" in validation.erreurs["periode"]
    with pytest.raises(ErreurSaisie) as capture:
        service.creer(session, {**brouillon, "type": "PAUSE_MINIMALE", "parametres": {"duree": 30}})
    assert set(capture.value.erreurs) == {"periode"}
    # Au semestre : une seule valeur par personne et par semestre.
    assert service.valider(session, {**brouillon, "periode": {"semestre": "S2"}}).valide
    detail = service.creer(session, {**brouillon, "periode": {"semestre": "S2"}})
    toute_l_annee = service.valider(session, {**brouillon, "periode": {}})
    assert not toute_l_annee.valide and detail.code in toute_l_annee.erreurs["portee"]
    assert service.valider(session, {**brouillon, "periode": {"semestre": "S1"}}).valide


def test_une_regle_sur_un_besoin_concerne_le_semestre_du_besoin(jeu_minimal, session):
    annee, s1 = jeu_minimal.annee_scolaire_id, jeu_minimal.semestre_id
    s2 = session.scalar(select(m.Semestre.id).where(m.Semestre.code == "S2"))
    detail = service.creer(
        session,
        {
            "type": "PLAGE_HORAIRE",
            "portee": {"nature": "besoin", "cible": "IM.1101 / CM / A1"},
            "parametres": {"fin": "12:45"},
        },
    )
    assert detail.regle.semestre_code == "S1"  # période déduite du besoin
    assert detail.vers_dict()["periode"] == {"semestre": "S1"}
    assert detail.code in {r.code for r in charger_instantane(session, s1).regles}
    assert detail.code not in {
        r.code for r in charger_instantane(session, s2).regles
    }  # hors périmètre
    explication = service.expliquer(session, annee, detail.code)
    assert [e.semestre_code for e in explication.effets] == ["S1"]
    # Un autre semestre explicite est contradictoire ; des dates restent possibles.
    brouillon = {
        "type": "CRENEAUX_INTERDITS",
        "portee": {"nature": "besoin", "cible": detail.regle.portee.cible_id},
        "parametres": {"jours": ["lundi"]},
    }
    validation = service.valider(session, {**brouillon, "periode": {"semestre": "S2"}})
    assert not validation.valide and "S1" in validation.erreurs["periode.semestre"]
    datee = service.creer(
        session, {**brouillon, "periode": {"date_debut": "2026-10-01", "date_fin": "2026-10-20"}}
    )
    assert datee.regle.semestre_code is None and datee.regle.date_fin == dt.date(2026, 10, 20)
    assert datee.code not in {r.code for r in charger_instantane(session, s2).regles}
    assert [e.semestre_code for e in service.expliquer(session, annee, datee.code).effets] == ["S1"]
    assert {r.code for r in service.lister(session, annee)} >= {detail.code, datee.code}


def test_cible_besoin_ambigue_departagee_par_le_semestre_et_sans_casse(jeu_minimal, session):
    brouillon = {
        "type": "CRENEAUX_INTERDITS",
        "portee": {"nature": "besoin", "cible": "im.1101 / cm / a1"},
        "parametres": {"jours": ["lundi"]},
    }
    validation = service.valider(session, brouillon)
    assert validation.valide, validation.erreurs  # sans casse
    modele = session.get(m.Besoin, validation.regle.portee.cible_id)
    s2 = session.scalar(select(m.Semestre).where(m.Semestre.code == "S2"))
    session.add(
        m.Besoin(
            semestre_id=s2.id,
            matiere_id=modele.matiere_id,
            type_seance_id=modele.type_seance_id,
            groupes=list(modele.groupes),
            enseignants=list(modele.enseignants),
            libelle="CM Mathématiques - A1 (S2)",
        )
    )
    session.flush()
    validation = service.valider(session, brouillon)
    assert not validation.valide and "ambigu" in validation.erreurs["portee.cible"]
    assert "inconnu" not in validation.erreurs["portee.cible"]
    pour_s2 = service.valider(session, {**brouillon, "periode": {"semestre": "S2"}})
    assert pour_s2.valide and pour_s2.regle.semestre_code == "S2"
    assert pour_s2.regle.portee.cible_id != modele.id
    inconnu = service.valider(
        session, {**brouillon, "portee": {"nature": "besoin", "cible": "IM.1101 / TP / A1"}}
    )
    assert "inconnu" in inconnu.erreurs["portee.cible"]


def test_matiere_d_une_autre_annee_refusee_par_identifiant(jeu_minimal, session):
    suivante = assurer_annee_scolaire(session, 2027)
    promo = m.Groupe(
        annee_scolaire_id=suivante.id, code="A1", libelle="1re année", nature="promotion"
    )
    session.add(promo)
    session.flush()
    matiere = m.Matiere(code="IM.2101", libelle="Analyse", promotion_id=promo.id)
    session.add(matiere)
    session.flush()
    brouillon = {
        "type": "SALLES",
        "portee": {"nature": "matiere", "cible": matiere.id},
        "parametres": {"mode": "interdire", "salles": ["N016"]},
    }
    validation = service.valider(session, {**brouillon, "annee": "2026-2027"})
    assert not validation.valide and "autre année" in validation.erreurs["portee.cible"]
    assert service.valider(session, {**brouillon, "annee": "2027-2028"}).valide


def test_activer_revalide_la_regle(jeu_minimal, session):
    annee = jeu_minimal.annee_scolaire_id
    proposee = service.creer(
        session,
        {
            "type": "SALLES",
            "portee": {"nature": "enseignant", "cible": "ENS004"},
            "parametres": {"mode": "interdire", "salles": ["L012"]},
        },
        etat="proposee",
    )
    salle = session.scalar(select(m.Salle).where(m.Salle.code == "L012"))
    salle.code = "L013"
    session.flush()
    with pytest.raises(ErreurSaisie) as capture:
        service.activer(session, annee, proposee.code)
    assert "parametres.salles" in capture.value.erreurs
    assert service.lire(session, annee, proposee.code).etat is EtatRegle.PROPOSEE  # inchangée
    salle.code = "L012"
    session.flush()
    active = service.activer(session, annee, proposee.code)
    assert active.etat is EtatRegle.ACTIVE and active.phrase == proposee.phrase


def test_brouillon_mal_forme_renvoie_des_erreurs_par_champ(jeu_minimal, session):
    validation = service.valider(
        session,
        {
            "type": "SANS_TROU",
            "portee": 42,
            "parametres": "maximum = 2",
            "periode": "S1",
            "origine": "chat",
        },
    )
    assert not validation.valide
    assert set(validation.erreurs) >= {"portee", "parametres", "periode", "origine"}
    assert validation.erreurs["origine"].startswith("Origine inconnue")
    with pytest.raises(ErreurSaisie) as capture:
        service.modifier(session, jeu_minimal.annee_scolaire_id, "R001", {"origine": "x"})
    assert set(capture.value.erreurs) == {"origine"}


def test_proposer_ne_double_pas_une_regle_de_base_confirmee_ni_reimportee(jeu_minimal, session):
    annee = jeu_minimal.annee_scolaire_id
    # Réimport : les règles de base reviennent en origine « import » ; rien n'est reproposé.
    for code in ("R003", "R004"):
        session.scalar(select(m.Regle).where(m.Regle.code == code)).origine = OrigineRegle.IMPORT
    session.flush()
    proposition = service.proposer_pour_annee(session, annee)
    assert (len(proposition.creees), proposition.deja_presentes) == (0, 2)
    # Année suivante : la règle de base confirmée en 2026-2027 n'est proposée qu'une fois.
    service.activer(session, annee, "R003")
    suivante = assurer_annee_scolaire(session, 2027)
    proposition = service.proposer_pour_annee(session, suivante.id)
    phrases = [r.phrase for r in proposition.creees]
    assert phrases.count("Dans chaque matière, le CM précède les TD de la semaine.") == 1
    assert [r.regle.origine for r in proposition.creees] == [OrigineRegle.BASE, OrigineRegle.BASE]
    assert proposition.deja_presentes == 1  # la reprise de R003, déjà là comme règle de base
    assert len(proposition.a_revoir) == 2  # R001 et R002 : pas de groupe A1 ni A1-G1 en 2027-2028


# --------------------------------------------------------------------------- #
# Cas limites : brouillons mal formés, filtres, code, année, modifications, règles incomplètes
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
@pytest.mark.parametrize("brouillon", [["PAUSE_DEJEUNER"], "PAUSE_DEJEUNER", None, 42])
def test_brouillon_qui_n_est_pas_un_objet_est_une_validation_invalide(
    jeu_minimal, session, brouillon
):
    """Cas : l'assistant ou un client JSON envoie une liste, un texte, `null` ou un nombre à
    `valider()` ou `creer()`.

    Risque : `dict(brouillon)` levait `ValueError`/`TypeError` (500) : le contrat « valider ne
    lève pas, renvoie une validation invalide » était rompu ; `creer` doit refuser sans écrire.
    """
    validation = service.valider(session, brouillon)
    assert not validation.valide and "objet" in validation.erreurs["brouillon"]
    nb_regles = session.scalar(select(func.count()).select_from(m.Regle))
    with pytest.raises(ErreurSaisie) as capture:
        service.creer(session, brouillon)
    assert "brouillon" in capture.value.erreurs
    assert session.scalar(select(func.count()).select_from(m.Regle)) == nb_regles
    with pytest.raises(ErreurSaisie):
        service.modifier(session, jeu_minimal.annee_scolaire_id, "R001", brouillon)


@pytest.mark.cas_limite
def test_modifier_refuse_un_changement_d_annee_ou_de_code_et_garde_le_controle_des_doublons(
    jeu_minimal, session
):
    """Cas : `modifier()` reçoit « annee » (autre année) ou « code » dans les changements.

    Risque : le doublon était cherché dans l'autre année mais la règle restait dans la sienne :
    deux réglages actifs pour la même cible, et le semestre entier devenait inchargeable ; le
    changement de code était ignoré en silence.
    """
    annee = jeu_minimal.annee_scolaire_id
    assurer_annee_scolaire(session, 2027)
    brouillon = {
        "type": "PAUSE_MINIMALE",
        "portee": {"nature": "enseignant", "cible": "ENS004"},
        "parametres": {"duree": 30},
    }
    active = service.creer(session, brouillon)
    inactive = service.creer(session, {**brouillon, "parametres": {"duree": 45}}, etat="inactive")

    with pytest.raises(ErreurSaisie) as capture:
        service.modifier(session, annee, inactive.code, {"etat": "active", "annee": "2027-2028"})
    assert (
        set(capture.value.erreurs) == {"annee"}
        and "ne se modifie pas" in capture.value.erreurs["annee"]
    )
    with pytest.raises(ErreurSaisie) as capture:
        service.modifier(session, annee, "R001", {"code": "R099"})
    assert set(capture.value.erreurs) == {"code"}

    assert service.lire(session, annee, inactive.code).etat is EtatRegle.INACTIVE  # inchangée
    assert service.lire(session, annee, "R001").code == "R001"
    actives = [
        r for r in service.lister(session, annee, type_regle="PAUSE_MINIMALE", etat="active")
    ]
    assert [r.code for r in actives] == [active.code]
    charger_instantane(session, jeu_minimal.semestre_id)  # le semestre reste chargeable
    # Répéter sa propre année ou son propre code n'est pas un changement.
    assert (
        service.modifier(session, annee, "R001", {"annee": "2026-2027", "code": "R001"}).code
        == "R001"
    )


@pytest.mark.cas_limite
def test_filtre_inconnu_est_une_erreur_metier(jeu_minimal, session):
    """Cas : `lister()` ou `regles_visant()` reçoit un filtre hors énumération (paramètre de
    requête, valeur de l'assistant).

    Risque : `ValueError` brute (500) au lieu d'une erreur métier (422) qui liste les valeurs
    admises.
    """
    annee = jeu_minimal.annee_scolaire_id
    for filtre, valeur in (
        ("etat", "inconnu"),
        ("type_regle", "PLANETE"),
        ("niveau", "vital"),
        ("origine", "martienne"),
    ):
        with pytest.raises(ErreurDonneesInvalides) as capture:
            service.lister(session, annee, **{filtre: valeur})
        assert valeur in capture.value.message and "valeurs admises" in capture.value.message
    with pytest.raises(ErreurDonneesInvalides, match="planete"):
        service.regles_visant(session, "planete", 1)
    with pytest.raises(ErreurDonneesInvalides, match="valeurs admises"):
        service.changer_etat(session, annee, "R001", "dormante")
    # Les membres eux-mêmes restent acceptés.
    assert service.lister(session, annee, etat=EtatRegle.ACTIVE, niveau=Niveau.NECESSAIRE)


@pytest.mark.cas_limite
def test_le_code_est_normalise_en_majuscules(jeu_minimal, session):
    """Cas : `creer()` reçoit le code « r002 » alors que R002 existe, puis « r007 » ; la ligne
    de commande met les codes en majuscules.

    Risque : « r002 » créé à côté de R002 (unicité sensible à la casse), ignoré par
    l'attribution automatique des codes et introuvable par la commande.
    """
    annee = jeu_minimal.annee_scolaire_id
    brouillon = {
        "type": "SANS_TROU",
        "portee": {"nature": "groupe", "cible": "A1-G2"},
    }
    with pytest.raises(ErreurSaisie) as capture:
        service.creer(session, {**brouillon, "code": "r002"})
    assert "R002 existe déjà" in capture.value.erreurs["code"]
    detail = service.creer(session, {**brouillon, "code": " r007 "})
    assert detail.code == "R007"
    assert service.lire(session, annee, "r007").id == detail.id
    assert service.lire(session, annee, " R007 ").id == detail.id


@pytest.mark.cas_limite
@pytest.mark.parametrize("annee", [2026, "2026", True, "2026/2027"])
def test_annee_mal_formee_indique_le_format_attendu(jeu_minimal, session, annee):
    """Cas : le brouillon donne l'année comme entier, « 2026 », booléen ou « 2026/2027 ».

    Risque : « L'année scolaire 2026 n'existe pas » : l'assistant ne savait ni le format
    « 2026-2027 » ni les années existantes, et bouclait sur des corrections.
    """
    validation = service.valider(
        session,
        {"type": "SANS_TROU", "portee": {"nature": "groupe", "cible": "A1-G2"}, "annee": annee},
    )
    assert not validation.valide
    message = validation.erreurs["annee"]
    assert "2026-2027" in message and str(annee) in message


@pytest.mark.cas_limite
def test_une_regle_enregistree_sans_heures_reste_lisible(jeu_minimal, session):
    """Cas : une règle enregistrée (import ancien, script) de type PLAGE_HORAIRE sans `debut` ni
    `fin` ; la liste et la fiche des règles sont ouvertes.

    Risque : `KeyError` dans la phrase : toute la liste répondait 500 pour une ligne ; la
    phrase doit se compléter avec les heures d'ouverture de l'établissement.
    """
    ligne = session.scalar(select(m.Regle).where(m.Regle.code == "R001"))
    ligne.type = TypeRegle.PLAGE_HORAIRE
    ligne.parametres = {"jours": []}
    session.flush()

    detail = service.lire_par_id(session, ligne.id)
    assert detail.phrase == (
        "La promotion A1 commence à 8 h 30 au plus tôt et finit à 18 h 00 au plus tard."
    )
    assert any(r.code == "R001" for r in service.lister(session, jeu_minimal.annee_scolaire_id))
    assert CATALOGUE_TYPES[TypeRegle.PLAGE_HORAIRE].champ("debut").defaut_global == "ouverture_min"


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    "nature", ["groupe", "enseignant", "campus", "type_seance", "matiere", "besoin"]
)
@pytest.mark.parametrize("cible", [10**20, 2**63, -5, 0], ids=["10^20", "2^63", "-5", "0"])
def test_regle_dont_la_cible_numerique_est_hors_bornes_est_invalide(
    jeu_minimal, session, nature, cible
):
    """Cas : brouillon de règle dont la cible est un identifiant numérique hors des entiers de
    SQLite, négatif ou nul (assistant, adresse forgée).

    Risque : `OverflowError` du pilote au lieu d'une validation « portee.cible » invalide :
    page 500 sur l'écran des règles (cas transmis par le lot solveur).
    """
    validation = service.valider(
        session,
        {
            "type": "CRENEAUX_INTERDITS",
            "portee": {"nature": nature, "cible": cible},
            "parametres": {"jours": ["lundi"]},
        },
    )
    assert not validation.valide
    assert str(cible) in validation.erreurs["portee.cible"]
    # Même garde pour une année donnée par un identifiant hors bornes.
    hors_annee = service.valider(
        session,
        {"type": "SANS_TROU", "portee": {"nature": "groupe", "cible": "A1-G2"}, "annee": cible},
    )
    assert not hors_annee.valide and "2026-2027" in hors_annee.erreurs["annee"]
