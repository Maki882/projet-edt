"""Ligne de commande : commandes générales et mécanisme d'extension."""

from __future__ import annotations

import datetime as dt
import sqlite3
import sys
import types
from contextlib import closing

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
from edt.services import generation, jeux


def test_version(lanceur):
    resultat = lanceur.invoke(cli.app, ["version"])
    assert resultat.exit_code == 0
    assert __version__ in resultat.output


def test_init_db_cree_la_base(lanceur, environnement):
    assert not base_initialisee(obtenir_moteur())

    resultat = lanceur.invoke(cli.app, ["init-db"])

    assert resultat.exit_code == 0, resultat.output
    assert "Base initialisée" in resultat.output
    assert "Étape suivante" in resultat.output and "edt jeu --taille moyen" in resultat.output
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
    assert "16 besoins" in premier.output and "edt generer --semestre" in premier.output
    relance = lanceur.invoke(cli.app, ["init-db"])  # base garnie : plus de jeu à proposer
    assert "Étape suivante : lancer « python -m edt serve »" in relance.output
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(Besoin)) == 16

    second = lanceur.invoke(cli.app, ["jeu-minimal"])
    assert second.exit_code == cli.CODE_ERREUR_METIER
    assert "contient déjà" in second.output


def test_jeu_minimal_sans_base_initialisee(lanceur, environnement):
    resultat = lanceur.invoke(cli.app, ["jeu-minimal"])
    assert resultat.exit_code == cli.CODE_ERREUR_METIER
    assert "init-db" in resultat.output


def test_doctor(lanceur, base, port_libre):
    resultat = lanceur.invoke(cli.app, ["doctor"])

    assert resultat.exit_code == 0, resultat.output
    for mot in ("Python", "OR-Tools", "Base", "Journaux", "Port"):
        assert mot in resultat.output
    assert "ECHEC" not in resultat.output
    assert "Tout est prêt." in resultat.output


def test_doctor_signale_une_base_non_initialisee(lanceur, environnement, port_libre):
    resultat = lanceur.invoke(cli.app, ["doctor"])
    assert resultat.exit_code == 0  # un avertissement, pas un échec
    assert "ATTENTION" in resultat.output and "non initialisée" in resultat.output
    assert "Tout est prêt" not in resultat.output and "1 avertissement(s)" in resultat.output


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

    Risque : trace brute et code 2, et le script de déploiement enchaînerait sur « --reset-base »
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
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_base_illisible_le_remede_annonce_permet_d_en_sortir(
    lanceur, base, environnement, monkeypatch, corrompre_base, env
):
    """Cas : le fichier de la base n'est plus une base SQLite (abîmé ou remplacé), et
    `init-db --reset --oui` est lancé pour la recréer, en développement puis en production.

    Risque : en développement, le refus conseille cette même commande (l'utilisateur tourne en
    rond) ; en production, il conclut « libérer de l'espace disque ou corriger les droits »,
    qui ne sert à rien. Le remède annoncé doit permettre de recréer la base.
    """
    monkeypatch.setenv("EDT_ENV", env)
    chemin = corrompre_base()

    refus = lanceur.invoke(cli.app, ["init-db", "--reset", "--oui"])

    assert refus.exit_code == cli.CODE_ERREUR_METIER, refus.output
    sortie = " ".join(refus.output.split())
    assert "init-db --reset" not in sortie and "espace disque" not in sortie, sortie
    assert "de côté" in sortie and "« python -m edt init-db »" in sortie, sortie
    # Le remède annoncé : mettre le fichier de côté, puis « init-db ».
    chemin.rename(chemin.with_name("edt-illisible.db"))
    recree = lanceur.invoke(cli.app, ["init-db"])
    assert recree.exit_code == 0, recree.output


@pytest.mark.cas_limite
def test_init_db_reset_exige_une_confirmation_dans_un_terminal_et_aucun_calcul_en_cours(
    lanceur, jeu_minimal, environnement, monkeypatch, compter
):
    """Cas : `init-db --reset` tapé dans un terminal, puis demandé pendant qu'un calcul tourne
    dans un autre processus, puis en production hors calcul.

    Risque : base de production effacée par mégarde sans question ni sauvegarde, calcul en
    cours qui meurt « no such table » ; ici refus dans les deux premiers cas et copie
    « sauvegardes/edt-<date>-avant-reset.db » dans le troisième.
    """
    monkeypatch.setattr(module_base, "_entree_interactive", lambda: True)
    refus = lanceur.invoke(cli.app, ["init-db", "--reset"], input="n\n")
    assert refus.exit_code != 0
    assert compter(Salle) == jeu_minimal.nb_salles  # rien d'effacé

    # Un calcul en cours dans un autre processus (vivant) : refus, même avec --oui. La
    # vivacité du processus est simulée : elle ne dépend pas de l'arbre des processus du test.
    pid_vivant = 4242
    monkeypatch.setattr(generation, "_processus_vivant", lambda pid: pid == pid_vivant)
    with session_base() as session:
        calcul = Calcul(
            semestre_id=jeu_minimal.semestre_id,
            etat=EtatCalcul.EN_COURS,
            demarre_le=dt.datetime.now(),
            statistiques={"pid": pid_vivant},
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
    (copie,) = (environnement / "sauvegardes").glob("edt-*-avant-reset.db")
    with closing(sqlite3.connect(copie)) as sauvegarde:
        assert (
            sauvegarde.execute("SELECT count(*) FROM salle").fetchone()[0] == jeu_minimal.nb_salles
        )


@pytest.mark.cas_limite
def test_init_db_reset_en_production_n_efface_rien_si_la_sauvegarde_echoue(
    lanceur, jeu_minimal, environnement, monkeypatch, compter
):
    """Cas : `init-db --reset --oui` en production alors que la sauvegarde préalable échoue
    (disque plein, dossier « sauvegardes » non inscriptible).

    Risque : la remise à zéro détruirait une base qui n'a pas été sauvegardée ; la commande
    doit s'arrêter avant, code 1, en nommant la sauvegarde et le remède, sans fichier partiel.
    """
    monkeypatch.setenv("EDT_ENV", "prod")
    (environnement / "sauvegardes").write_text("pas un dossier", encoding="utf-8")

    resultat = lanceur.invoke(cli.app, ["init-db", "--reset", "--oui"])

    assert resultat.exit_code == cli.CODE_ERREUR_METIER, resultat.output
    assert "sauvegarde de la base" in resultat.output and "remise à zéro annulée" in resultat.output
    assert compter(Salle) == jeu_minimal.nb_salles
    assert not list(environnement.rglob("*.partiel"))


@pytest.mark.cas_limite
def test_init_db_reset_est_refuse_pendant_un_chargement_de_jeu(
    lanceur, jeu_minimal, environnement, compter
):
    """Cas : `init-db --reset --oui` lancé pendant qu'un chargement de jeu fictif tient le
    verrou des opérations sur la base (écran de configuration, commande `jeu`).

    Risque : tables supprimées sous le chargement en cours, et serveur qui ignore la remise
    à zéro (pas de fichier marqueur) ; la commande doit refuser, code 1, sans rien effacer.
    """
    with jeux._verrou_base:
        resultat = lanceur.invoke(cli.app, ["init-db", "--reset", "--oui"])

    assert resultat.exit_code == cli.CODE_ERREUR_METIER, resultat.output
    assert "déjà en cours" in resultat.output
    assert compter(Salle) == jeu_minimal.nb_salles


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
    assert compter(Salle) == 0 and not list(environnement.rglob("edt-*-avant-reset.db"))


@pytest.mark.cas_limite
def test_init_db_reset_sans_sauvegarde_ne_double_pas_celle_du_deploiement(
    lanceur, jeu_minimal, environnement, monkeypatch, compter
):
    """Cas : `init-db --reset --oui --sans-sauvegarde` en production, lancé par le déploiement
    qui vient de faire et de vérifier sa propre sauvegarde.

    Risque : une seconde copie brute de la base à chaque déploiement avec remise à zéro, qui
    remplit le disque du serveur.
    """
    monkeypatch.setenv("EDT_ENV", "prod")

    resultat = lanceur.invoke(cli.app, ["init-db", "--reset", "--oui", "--sans-sauvegarde"])

    assert resultat.exit_code == 0, resultat.output
    assert "Sauvegarde" not in resultat.output and compter(Salle) == 0
    assert not list(environnement.rglob("edt-*-avant-reset.db"))


@pytest.mark.cas_limite
def test_doctor_n_attend_pas_une_base_occupee_par_une_autre_ecriture(
    lanceur, base, delai_verrou_court, verrou_ecriture
):
    """Cas : `doctor` est lancé pendant qu'un autre processus écrit (calcul, chargement).

    Risque : doctor attendrait le délai de verrou puis conclurait « écriture impossible »
    (code 1) pour une base saine ; l'écriture d'essai est dite non vérifiée, avec le remède.
    """
    with verrou_ecriture():
        resultat = lanceur.invoke(cli.app, ["doctor"])

    assert resultat.exit_code == 0, resultat.output
    (ligne,) = [x for x in resultat.output.splitlines() if "] Écriture" in x]
    assert "ATTENTION" in ligne and "non vérifiée" in ligne and "doctor" in ligne


@pytest.mark.cas_limite
def test_doctor_signale_une_base_en_lecture_seule(lanceur, base, environnement, lecture_seule):
    """Cas : `edt.db` a été copié avec les droits d'un autre utilisateur (lecture seule).

    Risque : doctor dirait « Tout est prêt » alors qu'aucune écriture n'est possible ; il
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
    lanceur, base, environnement, port_libre
):
    """Cas : `<EDT_LOG_DIR>/generations` est un fichier (ou n'est pas inscriptible) alors que le
    dossier racine des journaux va bien.

    Risque : doctor dirait « Journaux accessibles » et « Tout est prêt » alors qu'aucun calcul
    n'aurait de journal.
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
