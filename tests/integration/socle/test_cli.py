"""Ligne de commande : commandes du socle et mécanisme d'extension."""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import sys
import types

import pytest
import typer
from sqlalchemy import func, select
from tests.outils.socle.extension_cli import NOM_MODULE_ESSAI

from edt import __version__, cli
from edt.commandes import base as module_base
from edt.db.initialisation import base_initialisee
from edt.db.modeles import Besoin, Calcul, Parametre, Salle
from edt.db.moteur import liberer_moteurs, obtenir_moteur, session_base
from edt.domaine.enumerations import EtatCalcul
from edt.erreurs import ErreurRegleMetier


def test_version(lanceur):
    resultat = lanceur.invoke(cli.app, ["version"])
    assert resultat.exit_code == 0
    assert __version__ in resultat.output


def test_init_db_cree_la_base(lanceur, environnement):
    assert not base_initialisee(obtenir_moteur())

    resultat = lanceur.invoke(cli.app, ["init-db"])

    assert resultat.exit_code == 0, resultat.output
    assert "Base initialisée" in resultat.output
    assert base_initialisee(obtenir_moteur())
    assert (environnement / "logs" / "edt.log").exists()


def test_init_db_reset_efface_les_donnees(lanceur, jeu_minimal):
    resultat = lanceur.invoke(cli.app, ["init-db", "--reset"])

    assert resultat.exit_code == 0, resultat.output
    assert "remise à zéro" in resultat.output
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(Salle)) == 0


def test_jeu_minimal_puis_refus_du_second_chargement(lanceur, base):
    premier = lanceur.invoke(cli.app, ["jeu-minimal"])
    assert premier.exit_code == 0, premier.output
    assert "16 besoins" in premier.output
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(Besoin)) == 16

    second = lanceur.invoke(cli.app, ["jeu-minimal"])
    assert second.exit_code == cli.CODE_ERREUR_METIER
    assert "contient déjà" in second.output


def test_jeu_minimal_sans_base_initialisee(lanceur, environnement):
    resultat = lanceur.invoke(cli.app, ["jeu-minimal"])
    assert resultat.exit_code == cli.CODE_ERREUR_METIER
    assert "init-db" in resultat.output


def test_doctor(lanceur, base):
    resultat = lanceur.invoke(cli.app, ["doctor"])

    assert resultat.exit_code == 0, resultat.output
    for mot in ("Python", "OR-Tools", "Base", "Journaux", "Port"):
        assert mot in resultat.output
    assert "ECHEC" not in resultat.output
    assert "Tout est prêt." in resultat.output


def test_doctor_signale_une_base_non_initialisee(lanceur, environnement):
    resultat = lanceur.invoke(cli.app, ["doctor"])
    assert resultat.exit_code == 0  # un avertissement, pas un échec
    assert "ATTENTION" in resultat.output and "non initialisée" in resultat.output


def test_configuration_invalide_donne_un_message_clair(lanceur, monkeypatch):
    monkeypatch.setenv("EDT_PORT", "pas-un-port")
    resultat = lanceur.invoke(cli.app, ["doctor"])
    assert resultat.exit_code == cli.CODE_ERREUR_METIER
    assert "EDT_PORT" in resultat.output


def test_module_de_commandes_decouvert(lanceur, monkeypatch):
    commandes = typer.Typer()

    @commandes.command("essai-refus")
    def essai_refus() -> None:
        """Commande d'essai qui échoue sur une règle métier."""
        raise ErreurRegleMetier("Refus d'essai.")

    @commandes.command("essai-panne")
    def essai_panne() -> None:
        """Commande d'essai qui plante."""
        raise RuntimeError("panne d'essai")

    module = types.ModuleType(NOM_MODULE_ESSAI)
    module.commandes = commandes
    monkeypatch.setitem(sys.modules, NOM_MODULE_ESSAI, module)
    reels = cli.modules_de_commandes()
    monkeypatch.setattr(cli, "modules_de_commandes", lambda: [*reels, NOM_MODULE_ESSAI])
    app = cli.construire_app()

    assert "essai-refus" in lanceur.invoke(app, ["--help"]).output
    refus = lanceur.invoke(app, ["essai-refus"])
    assert refus.exit_code == cli.CODE_ERREUR_METIER and "Refus d'essai." in refus.output
    panne = lanceur.invoke(app, ["essai-panne"])
    assert panne.exit_code == cli.CODE_ERREUR_INATTENDUE and "journal" in panne.output


# --------------------------------------------------------------------------- #
# Cas limites : base inaccessible, remise à zéro
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
@pytest.mark.parametrize("panne", ["verrou", "lecture_seule", "corrompue"])
def test_une_base_verrouillee_lecture_seule_ou_corrompue_donne_un_message_clair_et_le_code_1(
    lanceur,
    base,
    environnement,
    panne,
    delai_verrou_court,
    verrou_ecriture,
    lecture_seule,
    corrompre_base,
):
    """Cas : `init-db` ou `jeu-minimal` tourne alors qu'un autre processus tient le verrou
    d'écriture, que le fichier est en lecture seule, ou qu'il n'est plus une base SQLite.

    Risque : trace brute et code 2, et le script de déploiement enchaînait sur « --reset-base »
    (qui efface les données) pour un simple verrou ; il faut un message qui nomme la cause et
    le remède, code 1.
    """
    chemin = environnement / "edt.db"
    with session_base() as session:  # init-db aura une ligne à écrire
        session.delete(session.get(Parametre, "pause_min"))
    liberer_moteurs()

    def verifier(attendu: str) -> None:
        for commande in (["init-db"], ["jeu-minimal"]):
            resultat = lanceur.invoke(cli.app, commande)
            assert resultat.exit_code == cli.CODE_ERREUR_METIER, (commande, resultat.output)
            assert attendu in resultat.output, (commande, resultat.output)
            assert "Traceback" not in resultat.output and "inattendue" not in resultat.output

    if panne == "verrou":
        with verrou_ecriture():
            verifier("verrouillée")
    elif panne == "lecture_seule":
        with lecture_seule(chemin):
            verifier("lecture seule")
    else:
        corrompre_base()
        verifier("n'est pas une base SQLite")


@pytest.mark.cas_limite
def test_init_db_reset_exige_une_confirmation_dans_un_terminal_et_aucun_calcul_en_cours(
    lanceur, jeu_minimal, environnement, monkeypatch, compter
):
    """Cas : `init-db --reset` tapé dans un terminal, puis demandé pendant qu'un calcul tourne
    dans un autre processus, puis en production hors calcul.

    Risque : base de production effacée par mégarde sans question ni sauvegarde, calcul en
    cours qui meurt « no such table » ; ici refus dans les deux premiers cas et copie
    « edt-<date>-avant-reset.db » dans le troisième.
    """
    monkeypatch.setattr(module_base, "_entree_interactive", lambda: True)
    refus = lanceur.invoke(cli.app, ["init-db", "--reset"], input="n\n")
    assert refus.exit_code != 0
    assert compter(Salle) == jeu_minimal.nb_salles  # rien d'effacé

    # Un calcul en cours dans un autre processus (vivant) : refus, même avec --oui.
    with session_base() as session:
        calcul = Calcul(
            semestre_id=jeu_minimal.semestre_id,
            etat=EtatCalcul.EN_COURS,
            demarre_le=dt.datetime.now(),
            statistiques={"pid": os.getppid()},
        )
        session.add(calcul)
        session.flush()
        calcul_id = calcul.id
    pendant = lanceur.invoke(cli.app, ["init-db", "--reset", "--oui"])
    assert pendant.exit_code == cli.CODE_ERREUR_METIER, pendant.output
    assert "calcul" in pendant.output and compter(Salle) == jeu_minimal.nb_salles
    with session_base() as session:
        session.get(Calcul, calcul_id).etat = EtatCalcul.ERREUR

    # Hors calcul, en production : sauvegarde du fichier puis remise à zéro.
    monkeypatch.setenv("EDT_ENV", "prod")
    fait = lanceur.invoke(cli.app, ["init-db", "--reset", "--oui"])
    assert fait.exit_code == 0, fait.output
    assert "Sauvegarde" in fait.output and compter(Salle) == 0
    (copie,) = environnement.glob("edt-*-avant-reset.db")
    with sqlite3.connect(copie) as sauvegarde:
        assert (
            sauvegarde.execute("SELECT count(*) FROM salle").fetchone()[0] == jeu_minimal.nb_salles
        )


@pytest.mark.cas_limite
def test_init_db_reset_non_interactif_ne_pose_pas_de_question(
    lanceur, jeu_minimal, environnement, compter
):
    """Cas : `init-db --reset` lancé par un script (déploiement), sans terminal, en développement.

    Risque : une question posée à un script le bloquerait ou le ferait échouer ; en
    développement, pas de copie de sauvegarde qui encombre le dossier.
    """
    resultat = lanceur.invoke(cli.app, ["init-db", "--reset"])
    assert resultat.exit_code == 0, resultat.output
    assert compter(Salle) == 0 and not list(environnement.glob("edt-*-avant-reset.db"))


@pytest.mark.cas_limite
def test_doctor_signale_une_base_en_lecture_seule(lanceur, base, environnement, lecture_seule):
    """Cas : `edt.db` a été copié avec les droits d'un autre utilisateur (lecture seule).

    Risque : doctor disait « Tout est prêt » alors qu'aucune écriture n'était possible ; il
    doit échouer en nommant la lecture seule.
    """
    liberer_moteurs()
    with lecture_seule(environnement / "edt.db"):
        resultat = lanceur.invoke(cli.app, ["doctor"])
    assert resultat.exit_code == 1, resultat.output
    assert "ECHEC" in resultat.output and "lecture seule" in resultat.output


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    "commande",
    [
        ["generer", "--semestre", "999"],
        ["generer", "--semestre", "-1"],
        ["pre-verifier", "--semestre", "0"],
        ["instancier", "--version", "999"],
    ],
    ids=lambda c: " ".join(c),
)
def test_commandes_de_calcul_avec_un_identifiant_inexistant(
    lanceur, jeu_minimal, commande, compter
):
    """Cas : `generer`, `pre-verifier` ou `instancier` reçoivent un identifiant inexistant,
    nul ou négatif.

    Risque : trace Python (code 2) ou ligne de calcul créée pour un semestre inexistant ; il
    faut le code 1, « n'existe pas », et aucun calcul enregistré.
    """
    resultat = lanceur.invoke(cli.app, commande)
    assert resultat.exit_code == cli.CODE_ERREUR_METIER, resultat.output
    assert "n'existe pas" in resultat.output and "Traceback" not in resultat.output
    assert compter(Calcul) == 0


@pytest.mark.cas_limite
def test_doctor_signale_le_dossier_des_journaux_de_calcul_inutilisable(
    lanceur, base, environnement
):
    """Cas : `<EDT_LOG_DIR>/generations` est un fichier (ou n'est pas inscriptible) alors que le
    dossier racine des journaux va bien.

    Risque : doctor disait « Journaux accessibles » et « Tout est prêt » alors qu'aucun calcul
    n'aurait de journal (cas transmis par le lot solveur).
    """
    (environnement / "logs").mkdir(exist_ok=True)
    (environnement / "logs" / "generations").write_text("pas un dossier", encoding="utf-8")

    resultat = lanceur.invoke(cli.app, ["doctor"])
    assert resultat.exit_code == 1, resultat.output
    lignes = [ligne for ligne in resultat.output.splitlines() if "generations" in ligne]
    assert lignes and all("ECHEC" in ligne for ligne in lignes)
    assert "aucun calcul n'aurait de journal" in resultat.output

    (environnement / "logs" / "generations").unlink()
    repare = lanceur.invoke(cli.app, ["doctor"])
    assert repare.exit_code == 0, repare.output
    assert "Journaux de calcul" in repare.output and "Tout est prêt." in repare.output
