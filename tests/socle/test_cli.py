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

from edt import __version__, cli
from edt.commandes import base as module_base
from edt.db.initialisation import base_initialisee
from edt.db.modeles import Besoin, Calcul, Parametre, Salle
from edt.db.moteur import liberer_moteurs, obtenir_moteur, session_base
from edt.domaine.enumerations import EtatCalcul
from edt.erreurs import ErreurConfiguration, ErreurRegleMetier

NOM_MODULE_ESSAI = "edt.commandes.essai_socle"


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


def test_module_de_commandes_sans_objet_commandes_refuse(monkeypatch):
    monkeypatch.setitem(sys.modules, NOM_MODULE_ESSAI, types.ModuleType(NOM_MODULE_ESSAI))
    monkeypatch.setattr(cli, "modules_de_commandes", lambda: [NOM_MODULE_ESSAI])
    with pytest.raises(ErreurConfiguration, match="commandes"):
        cli.construire_app()


def test_point_d_entree_renvoie_le_code_de_sortie(base):
    assert cli.main(["version"]) == 0
    assert cli.main(["commande-inconnue"]) == 2


def test_les_sorties_sont_forcees_en_utf8(monkeypatch):
    """Sous Windows, une sortie redirigée doit rester lisible : les accents sortent en UTF-8."""
    import io
    import sys

    from edt.cli import _forcer_sorties_utf8

    tampon = io.BytesIO()
    flux = io.TextIOWrapper(tampon, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", flux)

    _forcer_sorties_utf8()
    print("séances placées", file=flux, flush=True)

    assert flux.encoding.lower().replace("-", "") == "utf8"
    assert tampon.getvalue().decode("utf-8").strip() == "séances placées"


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
@pytest.mark.xfail(
    strict=True,
    reason="à corriger par le lot solveur : src/edt/commandes/generation.py (niveaux_choisis) : "
    "accepter la casse et les espaces des codes et des niveaux (« pause », « Pause = Facultatif ») "
    "comme le fait --regle",
)
def test_options_de_niveau_insensibles_a_la_casse_et_aux_espaces():
    """Cas : `generer -d pause --niveau " Pause = Facultatif "` (casse et espaces libres).

    Risque : refus surprenant alors que `--regle r007=Facultatif` tolère casse et espaces ;
    fragile pour les scripts.
    """
    from edt.commandes.generation import niveaux_choisis
    from edt.domaine.contraintes import CodeContrainte, Niveau

    assert CodeContrainte.PAUSE not in niveaux_choisis(["pause"], None)
    assert (
        niveaux_choisis(None, [" Pause = Facultatif "])[CodeContrainte.PAUSE] is Niveau.FACULTATIF
    )
