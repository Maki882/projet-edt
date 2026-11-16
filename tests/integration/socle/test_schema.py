"""Schéma périmé vu par les interfaces : init-db, verifier-schema, doctor, jeu-minimal et /sante."""

from __future__ import annotations

from sqlalchemy import func, select
from tests.outils.socle.schema import (
    COLONNE_ABSENTE,
    COLONNE_EN_TROP,
    ajouter_colonne,
    recreer_table,
    supprimer_table,
)

from edt import cli
from edt.db.base import Base
from edt.db.initialisation import (
    MARCHE_A_SUIVRE_SCHEMA_PERIME,
    base_initialisee,
    ecarts_schema,
    tables_manquantes,
)
from edt.db.modeles import Besoin, Parametre
from edt.db.moteur import obtenir_moteur, session_base


def _compter(modele) -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(modele))


def test_init_db_refuse_un_schema_perime_sans_rien_modifier(lanceur, jeu_minimal):
    ajouter_colonne("besoin", "ancien_champ VARCHAR(10)")
    supprimer_table("occurrence")  # une table manquante qu'init-db créerait d'ordinaire
    with session_base() as session:
        session.delete(session.get(Parametre, "pause_min"))  # une donnée par défaut à reposer
    nb_parametres = _compter(Parametre)

    resultat = lanceur.invoke(cli.app, ["init-db"])

    assert resultat.exit_code == 1
    sortie = resultat.output
    assert COLONNE_EN_TROP in sortie
    assert "--reset" in sortie and "EFFACE" in sortie
    assert sortie.rindex(COLONNE_EN_TROP) < sortie.rindex(MARCHE_A_SUIVRE_SCHEMA_PERIME)
    assert "Base initialisée" not in sortie
    # Rien n'a bougé : ni table créée, ni donnée par défaut reposée, ni donnée perdue.
    assert tables_manquantes(obtenir_moteur()) == ["occurrence"]
    assert _compter(Parametre) == nb_parametres
    assert _compter(Besoin) == jeu_minimal.nb_besoins
    assert ecarts_schema(obtenir_moteur()) == [COLONNE_EN_TROP]


def test_init_db_reset_repare_un_schema_perime(lanceur, jeu_minimal):
    ajouter_colonne("besoin", "ancien_champ VARCHAR(10)")
    recreer_table("creneau", sans="demi_journee")

    resultat = lanceur.invoke(cli.app, ["init-db", "--reset"])

    assert resultat.exit_code == 0, resultat.output
    assert "remise à zéro" in resultat.output
    moteur = obtenir_moteur()
    assert ecarts_schema(moteur) == []
    assert base_initialisee(moteur)
    assert _compter(Besoin) == 0  # le prix à payer : les données sont effacées


def test_verifier_schema_a_jour(lanceur, base):
    resultat = lanceur.invoke(cli.app, ["verifier-schema"])

    assert resultat.exit_code == 0
    assert resultat.stdout.strip() == "schéma à jour"  # une seule ligne sur la sortie standard


def test_verifier_schema_perime_liste_les_ecarts_sans_rien_modifier(lanceur, base):
    ajouter_colonne("besoin", "ancien_champ VARCHAR(10)")
    recreer_table("salle", sans="etage")

    resultat = lanceur.invoke(cli.app, ["verifier-schema"])

    assert resultat.exit_code == 1
    lignes = resultat.stdout.strip().splitlines()  # la sortie standard, sans les journaux
    assert lignes[0].startswith("schéma périmé : 2 écart(s)")
    assert lignes[1:3] == [f"  - {COLONNE_EN_TROP}", f"  - {COLONNE_ABSENTE}"]
    assert lignes[3] == MARCHE_A_SUIVRE_SCHEMA_PERIME
    assert ecarts_schema(obtenir_moteur()) == [COLONNE_EN_TROP, COLONNE_ABSENTE]


def test_verifier_schema_base_inaccessible(lanceur, environnement):
    (environnement / "edt.db").write_bytes(b"ceci n'est pas une base SQLite" * 40)

    resultat = lanceur.invoke(cli.app, ["verifier-schema"])

    assert resultat.exit_code == 2
    assert "Base inaccessible" in resultat.stderr
    assert resultat.stdout == ""


def test_verifier_schema_avant_la_premiere_initialisation(lanceur, environnement):
    resultat = lanceur.invoke(cli.app, ["verifier-schema"])

    assert resultat.exit_code == 0  # rien à comparer : init-db créera les tables
    nombre = len(Base.metadata.tables)
    assert resultat.stdout.startswith(
        f"schéma à jour pour les tables existantes ; {nombre} table(s)"
    )
    assert "init-db" in resultat.stdout


def test_doctor_a_une_ligne_pour_le_schema(lanceur, base):
    a_jour = lanceur.invoke(cli.app, ["doctor"])
    assert a_jour.exit_code == 0, a_jour.output
    assert "Schéma à jour" in a_jour.output

    ajouter_colonne("besoin", "ancien_champ VARCHAR(10)")
    perime = lanceur.invoke(cli.app, ["doctor"])
    assert perime.exit_code == 1
    assert "ECHEC" in perime.output and "Schéma périmé : 1 écart(s)" in perime.output
    assert "Tout est prêt." not in perime.output


def test_jeu_minimal_refuse_une_base_au_schema_perime(lanceur, base):
    ajouter_colonne("besoin", "ancien_champ VARCHAR(10)")

    resultat = lanceur.invoke(cli.app, ["jeu-minimal"])

    assert resultat.exit_code == cli.CODE_ERREUR_METIER
    assert COLONNE_EN_TROP in resultat.output
    assert _compter(Besoin) == 0


def test_sante_repond_503_quand_le_schema_est_perime(client):
    assert client.get("/sante").status_code == 200

    recreer_table("salle", sans="etage")
    reponse = client.get("/sante")

    assert reponse.status_code == 503
    corps = reponse.json()
    assert corps["statut"] == "degrade"
    assert corps["base"]["accessible"] is True
    assert corps["base"]["initialisee"] is False
