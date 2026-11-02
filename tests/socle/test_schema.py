"""Schéma périmé : une table existante qui n'a plus les colonnes du modèle est signalée partout.

Il n'y a pas de migrations. Ces tests simulent une base créée par une
ancienne version du code, puis vérifient que rien ne se déclare « prêt » :
`init-db` refuse, `verifier-schema` et `doctor` échouent, `/sante` répond 503.
"""

from __future__ import annotations

import pytest
from sqlalchemy import UniqueConstraint, func, select
from sqlalchemy.schema import CreateIndex

from edt import cli
from edt.db.base import Base
from edt.db.initialisation import (
    MARCHE_A_SUIVRE_SCHEMA_PERIME,
    ErreurSchemaPerime,
    base_initialisee,
    ecarts_schema,
    initialiser_base,
    tables_manquantes,
)
from edt.db.modeles import Besoin, Parametre
from edt.db.moteur import obtenir_moteur, session_base
from edt.services.etat import diagnostiquer_base

COLONNE_EN_TROP = "table besoin : colonne en trop : ancien_champ"
COLONNE_ABSENTE = "table salle : colonne etage absente de la base"


def ajouter_colonne(table: str, definition: str) -> None:
    """Simule une colonne retirée du modèle : la base en a une de plus."""
    with obtenir_moteur().begin() as connexion:
        connexion.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {definition}")


def supprimer_table(table: str) -> None:
    with obtenir_moteur().begin() as connexion:
        connexion.exec_driver_sql(f"DROP TABLE {table}")


def recreer_table(
    nom: str, *, sans: str | None = None, definitions: dict[str, str] | None = None
) -> None:
    """Recrée une table (vide) comme l'aurait créée une ancienne version du modèle.

    `sans` retire une colonne (elle a été ajoutée au modèle depuis) ;
    `definitions` remplace la définition SQL de certaines colonnes.
    """
    moteur = obtenir_moteur()
    modele = Base.metadata.tables[nom]
    colonnes = []
    for colonne in modele.columns:
        if colonne.name == sans:
            continue
        definition = (definitions or {}).get(colonne.name)
        if definition is None:
            definition = colonne.type.compile(dialect=moteur.dialect)
            definition += "" if colonne.nullable else " NOT NULL"
        colonnes.append(f"{colonne.name} {definition}")
    cle = ", ".join(colonne.name for colonne in modele.primary_key.columns)
    colonnes.append(f"PRIMARY KEY ({cle})")
    # Unicités et index du modèle conservés : ces tests ne portent que sur les colonnes.
    for contrainte in modele.constraints:
        if isinstance(contrainte, UniqueConstraint):
            noms = ", ".join(c.name for c in contrainte.columns if c.name != sans)
            if noms:
                colonnes.append(f"CONSTRAINT {contrainte.name} UNIQUE ({noms})")
    with moteur.begin() as connexion:
        connexion.exec_driver_sql(f"DROP TABLE {nom}")
        # Une définition par ligne, comme dans les tables créées par SQLAlchemy.
        lignes = ",\n".join(colonnes)
        connexion.exec_driver_sql(f"CREATE TABLE {nom} (\n{lignes}\n)")
        for index in modele.indexes:
            connexion.exec_driver_sql(str(CreateIndex(index).compile(dialect=moteur.dialect)))


def _compter(modele) -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(modele))


# --------------------------------------------------------------------------- #
# Comparaison du schéma
# --------------------------------------------------------------------------- #


def test_base_a_jour_sans_ecart(base):
    moteur = obtenir_moteur()

    assert ecarts_schema(moteur) == []
    assert base_initialisee(moteur)
    etat = diagnostiquer_base()
    assert etat.initialisee and not etat.schema_perime
    assert etat.ecarts_schema == ()


def test_colonne_retiree_du_modele_signalee_en_trop(base):
    ajouter_colonne("besoin", "ancien_champ VARCHAR(10)")

    assert ecarts_schema(obtenir_moteur()) == [COLONNE_EN_TROP]
    assert not base_initialisee(obtenir_moteur())


def test_colonne_ajoutee_au_modele_signalee_absente(base):
    recreer_table("salle", sans="etage")

    assert ecarts_schema(obtenir_moteur()) == [COLONNE_ABSENTE]


def test_type_et_caractere_obligatoire_compares_mais_pas_la_longueur(base):
    recreer_table(
        "creneau",
        definitions={
            "jour": "VARCHAR(8) NOT NULL",  # entier dans le modèle
            "debut_min": "INTEGER",  # obligatoire dans le modèle
            "demi_journee": "VARCHAR(99) NOT NULL",  # seule la longueur change : sans effet
        },
    )

    assert ecarts_schema(obtenir_moteur()) == [
        "table creneau : colonne jour de type VARCHAR dans la base, INTEGER dans le modèle",
        "table creneau : colonne debut_min facultative dans la base, obligatoire dans le modèle",
    ]


def test_valeurs_d_une_enumeration_comparees(base):
    # La base date d'avant l'ajout de « apres_midi » au modèle, et connaît encore « soir ».
    recreer_table(
        "creneau",
        definitions={
            "demi_journee": "VARCHAR(32) NOT NULL CHECK (demi_journee IN ('matin', 'soir'))"
        },
    )

    assert ecarts_schema(obtenir_moteur()) == [
        "table creneau : colonne demi_journee : valeur(s) du modèle refusée(s) par la base : "
        "apres_midi",
        "table creneau : colonne demi_journee : valeur(s) de la base inconnue(s) du modèle : soir",
    ]


@pytest.mark.cas_limite
def test_un_index_unique_partiel_absent_est_un_ecart(base):
    """Cas : la base date d'avant l'index unique partiel « une seule version active par semestre ».

    Risque : `verifier-schema` disait « à jour », deux versions pouvaient être actives et
    l'instantané en choisissait une au hasard ; l'index absent doit être un écart de schéma.
    """
    # Base créée avant l'index qui garantit une seule version active par semestre.
    with obtenir_moteur().begin() as connexion:
        connexion.exec_driver_sql("DROP INDEX uq_version_edt_active_par_semestre")

    ecarts = ecarts_schema(obtenir_moteur())
    assert ecarts == [
        "table version_edt : index uq_version_edt_active_par_semestre absent de la base"
    ]
    assert not base_initialisee(obtenir_moteur())


@pytest.mark.cas_limite
def test_une_unicite_absente_ou_en_trop_est_un_ecart(base):
    """Cas : une table recréée par une ancienne version sans l'unicité du modèle (ou avec une autre).

    Risque : doublons acceptés (ou refusés à tort) sans que rien ne signale le schéma périmé.
    """
    moteur = obtenir_moteur()
    with moteur.begin() as connexion:
        connexion.exec_driver_sql("DROP TABLE campus")
        connexion.exec_driver_sql(
            "CREATE TABLE campus (id INTEGER NOT NULL, code VARCHAR(16) NOT NULL, "
            "nom VARCHAR(128) NOT NULL, prefixe_salle VARCHAR(16), PRIMARY KEY (id), "
            "CONSTRAINT uq_campus_nom UNIQUE (nom))"
        )

    assert ecarts_schema(moteur) == [
        "table campus : unicité (code) absente de la base",
        "table campus : unicité (nom) en trop dans la base",
    ]


def test_une_table_absente_n_est_pas_un_ecart_et_init_db_la_cree(base, aujourdhui):
    supprimer_table("occurrence")
    moteur = obtenir_moteur()

    assert ecarts_schema(moteur) == []
    assert tables_manquantes(moteur) == ["occurrence"]
    assert not base_initialisee(moteur)

    initialiser_base(aujourdhui=aujourdhui)
    assert base_initialisee(moteur)


def test_l_etat_de_la_base_porte_les_ecarts(base):
    ajouter_colonne("besoin", "ancien_champ VARCHAR(10)")

    etat = diagnostiquer_base()

    assert etat.accessible
    assert etat.schema_perime and etat.ecarts_schema == (COLONNE_EN_TROP,)
    assert not etat.initialisee  # périmée = pas prête à servir
    assert etat.tables_manquantes == ()
    assert etat.compteurs == {}


# --------------------------------------------------------------------------- #
# init-db
# --------------------------------------------------------------------------- #


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


def test_initialiser_base_leve_une_erreur_qui_liste_les_ecarts(base, aujourdhui):
    recreer_table("salle", sans="etage")

    with pytest.raises(ErreurSchemaPerime) as capture:
        initialiser_base(aujourdhui=aujourdhui)

    assert capture.value.ecarts == (COLONNE_ABSENTE,)
    assert capture.value.details == (COLONNE_ABSENTE,)
    assert str(capture.value).endswith(MARCHE_A_SUIVRE_SCHEMA_PERIME)


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


# --------------------------------------------------------------------------- #
# verifier-schema, doctor, jeu-minimal
# --------------------------------------------------------------------------- #


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
    assert resultat.stdout.startswith("schéma à jour pour les tables existantes ; 29 table(s)")
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


# --------------------------------------------------------------------------- #
# Application web
# --------------------------------------------------------------------------- #


def test_sante_repond_503_quand_le_schema_est_perime(client):
    assert client.get("/sante").status_code == 200

    recreer_table("salle", sans="etage")
    reponse = client.get("/sante")

    assert reponse.status_code == 503
    corps = reponse.json()
    assert corps["statut"] == "degrade"
    assert corps["base"]["accessible"] is True
    assert corps["base"]["initialisee"] is False
