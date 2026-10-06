"""Niveaux de règles, temps 1 : la plomberie, des options du calcul jusqu'au suivi et à la commande.

Le moteur pose encore toutes les contraintes présentes en dur ; seule la
circulation des niveaux est vérifiée ici (options, enregistrement, suivi,
pré-vérifications, ligne de commande).
"""

from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import select
from tests.solveur.fabrique import OPTIONS, Fabrique, options, verifier_solution
from typer.testing import CliRunner

from edt import cli
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import (
    CATALOGUE,
    CodeContrainte,
    Niveau,
    niveaux_depuis_desactivees,
    niveaux_par_defaut,
    normaliser_niveaux,
)
from edt.domaine.enumerations import EtatCalcul, Gravite, ModeVolume
from edt.erreurs import ErreurDonneesInvalides
from edt.services import generation
from edt.solveur import OptionsSolveur, resoudre

C = CodeContrainte
DELAI = 60


def _generer(semestre_id: int, reglages: OptionsSolveur) -> generation.SuiviCalcul:
    return generation.attendre_calcul(generation.lancer_calcul(semestre_id, reglages), DELAI)


# --------------------------------------------------------------------------- #
# Options du calcul
# --------------------------------------------------------------------------- #


def test_options_sans_rien_dire_tous_les_niveaux_par_defaut():
    reglages = OptionsSolveur()
    assert reglages.niveaux == niveaux_par_defaut()
    assert set(reglages.niveaux) == set(CATALOGUE)
    assert reglages.contraintes_desactivees == frozenset()
    assert reglages.niveaux[C.PAUSE] is Niveau.IMPORTANT
    assert reglages.niveaux[C.SALLE_UNIQUE] is Niveau.NECESSAIRE


def test_contraintes_desactivees_seules_construisent_les_niveaux():
    """Les appelants d'avant les niveaux : une contrainte désactivée est une clé absente."""
    reglages = OptionsSolveur(contraintes_desactivees={"PAUSE", "EQUIPEMENT"})
    assert C.PAUSE not in reglages.niveaux and C.EQUIPEMENT not in reglages.niveaux
    assert reglages.niveaux == niveaux_depuis_desactivees({"PAUSE", "EQUIPEMENT"})
    assert reglages.contraintes_desactivees == {C.PAUSE, C.EQUIPEMENT}
    assert reglages.vers_dict()["niveaux"].keys() == {
        code.value for code in CATALOGUE if code not in (C.PAUSE, C.EQUIPEMENT)
    }


def test_les_niveaux_sont_la_seule_verite():
    """`contraintes_desactivees` est déduit des clés absentes ; une contradiction est refusée."""
    choisis = normaliser_niveaux({"PAUSE": "facultatif", "MAX_ENSEIGNANT": "necessaire"})
    del choisis[C.EQUIPEMENT]  # ignorée
    reglages = OptionsSolveur(niveaux=choisis)
    assert reglages.niveaux[C.PAUSE] is Niveau.FACULTATIF
    assert reglages.niveaux[C.MAX_ENSEIGNANT] is Niveau.NECESSAIRE
    assert reglages.contraintes_desactivees == {C.EQUIPEMENT}
    enregistre = reglages.vers_dict()["niveaux"]
    assert enregistre["PAUSE"] == "facultatif" and "EQUIPEMENT" not in enregistre
    # Les niveaux passent par les textes des formulaires et survivent à `replace`.
    assert OptionsSolveur(niveaux={"PAUSE": "important"}).niveaux == {C.PAUSE: Niveau.IMPORTANT}
    assert dataclasses.replace(reglages, optimiser=True).niveaux == reglages.niveaux
    with pytest.raises(ErreurDonneesInvalides, match="à la fois ignorée"):
        OptionsSolveur(contraintes_desactivees={"PAUSE"}, niveaux=choisis)


def test_une_contrainte_qui_definit_le_probleme_reste_necessaire():
    with pytest.raises(ErreurDonneesInvalides, match="définit le problème"):
        OptionsSolveur(niveaux=normaliser_niveaux({"SALLE_UNIQUE": "important"}))
    with pytest.raises(ErreurDonneesInvalides, match="Niveau inconnu"):
        OptionsSolveur(niveaux={"PAUSE": "souhaitable"})
    with pytest.raises(ErreurDonneesInvalides, match="Contrainte inconnue"):
        OptionsSolveur(niveaux={"INCONNUE": "important"})


def test_le_moteur_pose_en_dur_les_contraintes_presentes_quel_que_soit_leur_niveau():
    """Temps 1 : une contrainte importante ou facultative est encore respectée à la lettre."""
    f = Fabrique()
    f.salle()
    classe, martin = f.groupe("A1"), f.enseignant()
    f.besoin([classe], [martin], nombre=2)
    instantane = f.instantane()
    souples = normaliser_niveaux({"PAUSE": "facultatif"})
    places = verifier_solution(instantane, resoudre(instantane, options(niveaux=souples)))
    a, b = places["1-1"], places["1-2"]
    assert (
        a.jour != b.jour
        or a.fin + instantane.parametres.pause_min <= b.debut
        or (b.fin + instantane.parametres.pause_min <= a.debut)
    )
    # Clé absente : contrainte ignorée, comme avec `contraintes_desactivees`.
    sans = dict(niveaux_par_defaut())
    del sans[C.PAUSE]
    assert resoudre(instantane, options(niveaux=sans)).contraintes_desactivees == {C.PAUSE}


# --------------------------------------------------------------------------- #
# Enregistrement et suivi
# --------------------------------------------------------------------------- #


def test_les_niveaux_sont_enregistres_avec_le_calcul_et_rendus_au_suivi(jeu_minimal):
    choisis = normaliser_niveaux({"PAUSE": "facultatif"})
    del choisis[C.EQUIPEMENT]
    suivi = _generer(jeu_minimal.semestre_id, dataclasses.replace(OPTIONS, niveaux=choisis))
    assert suivi.etat is EtatCalcul.REUSSI, suivi.message
    assert suivi.niveaux == choisis
    assert suivi.contraintes_desactivees == ("EQUIPEMENT",)
    with session_base() as session:
        calcul = session.get(m.Calcul, suivi.id)
        assert calcul.parametres["niveaux"]["PAUSE"] == "facultatif"
        assert "EQUIPEMENT" not in calcul.parametres["niveaux"]
        assert calcul.contraintes_desactivees == ["EQUIPEMENT"]


def test_un_calcul_d_avant_les_niveaux_est_lu_avec_ses_contraintes_desactivees(jeu_minimal):
    suivi = _generer(
        jeu_minimal.semestre_id, dataclasses.replace(OPTIONS, contraintes_desactivees={"PAUSE"})
    )
    with session_base() as session:
        calcul = session.get(m.Calcul, suivi.id)
        parametres = dict(calcul.parametres)
        del parametres["niveaux"]  # comme un calcul enregistré avant les niveaux
        calcul.parametres = parametres
    relu = generation.etat_calcul(suivi.id)
    assert relu.niveaux == niveaux_depuis_desactivees({"PAUSE"})
    assert C.PAUSE not in relu.niveaux and relu.contraintes_desactivees == ("PAUSE",)


# --------------------------------------------------------------------------- #
# Pré-vérifications
# --------------------------------------------------------------------------- #


def test_pre_verifier_semestre_ne_regarde_que_les_contraintes_presentes(jeu_minimal):
    with session_base() as session:
        besoin = session.scalar(
            select(m.Besoin).where(m.Besoin.mode_volume == ModeVolume.PAR_SEMAINE)
        )
        besoin.nombre = 60  # aucune semaine ne peut contenir 60 séances
    with session_base() as session:
        erreurs = [
            a
            for a in generation.pre_verifier_semestre(session, jeu_minimal.semestre_id)
            if a.gravite is Gravite.ERREUR
        ]
        assert erreurs
        code = erreurs[0].code
        # Le niveau ne change rien à la pré-vérification : seule la présence compte.
        importante = {**niveaux_par_defaut(), code: Niveau.FACULTATIF}
        avec = generation.pre_verifier_semestre(
            session, jeu_minimal.semestre_id, niveaux=importante
        )
        assert [a.message for a in avec] == [a.message for a in erreurs] or any(
            a.code is code for a in avec
        )
        sans = {c: n for c, n in niveaux_par_defaut().items() if c is not code}
        ignoree = generation.pre_verifier_semestre(session, jeu_minimal.semestre_id, niveaux=sans)
        assert not any(a.code is code for a in ignoree)
        # `niveaux` prime sur `contraintes_desactivees`.
        prime = generation.pre_verifier_semestre(
            session, jeu_minimal.semestre_id, [code.value], niveaux=niveaux_par_defaut()
        )
        assert any(a.code is code for a in prime)


# --------------------------------------------------------------------------- #
# Ligne de commande
# --------------------------------------------------------------------------- #


def test_commande_generer_niveau(jeu_minimal):
    lanceur = CliRunner()
    semestre = str(jeu_minimal.semestre_id)
    resultat = lanceur.invoke(
        cli.app,
        ["generer", "--semestre", semestre, "--sans-optimiser", "--temps-limite", "30"]
        + [
            "--niveau",
            "PAUSE=facultatif",
            "--niveau",
            "MAX_ENSEIGNANT=necessaire",
            "-d",
            "EQUIPEMENT",
        ],
    )
    assert resultat.exit_code == 0, resultat.output
    assert "Règles : " in resultat.output
    assert "Pause entre deux séances : facultatif" in resultat.output
    assert "ignorée" in resultat.output
    assert "Maxima horaires des enseignants : nécessaire" in resultat.output
    with session_base() as session:
        (calcul,) = session.scalars(select(m.Calcul)).all()
        assert calcul.parametres["niveaux"]["PAUSE"] == "facultatif"
        assert calcul.parametres["niveaux"]["MAX_ENSEIGNANT"] == "necessaire"
        assert "EQUIPEMENT" not in calcul.parametres["niveaux"]

    # Sans option : rien de particulier à dire sur les règles.
    resultat = lanceur.invoke(
        cli.app, ["generer", "--semestre", semestre, "--sans-optimiser", "--temps-limite", "30"]
    )
    assert resultat.exit_code == 0 and "Règles : " not in resultat.output


@pytest.mark.parametrize(
    ("arguments", "attendu"),
    [
        (["--niveau", "PAUSE"], "CODE=niveau"),
        (["--niveau", "PAUSE=souhaitable"], "Niveau inconnu"),
        (["--niveau", "SALLE_UNIQUE=important"], "définit le problème"),
        (["--niveau", "PAUSE=facultatif", "-d", "PAUSE"], "à la fois ignorée"),
        (["--niveau", "INCONNUE=important"], "Contrainte inconnue"),
    ],
)
def test_commande_generer_niveau_refuse(jeu_minimal, arguments, attendu):
    resultat = CliRunner().invoke(
        cli.app, ["generer", "--semestre", str(jeu_minimal.semestre_id), *arguments]
    )
    assert resultat.exit_code == 1, resultat.output
    assert attendu in resultat.output
    with session_base() as session:
        assert session.scalars(select(m.Calcul)).all() == []  # rien n'a été lancé
