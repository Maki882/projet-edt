"""Schéma périmé : une table existante qui n'a plus les colonnes du modèle est signalée partout.

Il n'y a pas de migrations. Ces tests simulent une base créée par une
ancienne version du code, puis vérifient que rien ne se déclare « prêt » :
`init-db` refuse, `verifier-schema` et `doctor` échouent, `/sante` répond 503.
"""

from __future__ import annotations

import pytest
from tests.outils.socle.schema import (
    COLONNE_ABSENTE,
    COLONNE_EN_TROP,
    ajouter_colonne,
    recreer_table,
    supprimer_table,
)

from edt.db.initialisation import (
    MARCHE_A_SUIVRE_SCHEMA_PERIME,
    ErreurSchemaPerime,
    base_initialisee,
    ecarts_schema,
    initialiser_base,
    tables_manquantes,
)
from edt.db.moteur import obtenir_moteur
from edt.services.etat import diagnostiquer_base

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
    """Cas : la base n'a pas l'index unique partiel « une seule version active par semestre ».

    Risque : `verifier-schema` dirait « à jour », deux versions pourraient être actives et
    l'instantané en choisirait une au hasard ; l'index absent doit être un écart de schéma.
    """
    # Base sans l'index qui garantit une seule version active par semestre.
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


def test_initialiser_base_leve_une_erreur_qui_liste_les_ecarts(base, aujourdhui):
    recreer_table("salle", sans="etage")

    with pytest.raises(ErreurSchemaPerime) as capture:
        initialiser_base(aujourdhui=aujourdhui)

    assert capture.value.ecarts == (COLONNE_ABSENTE,)
    assert capture.value.details == (COLONNE_ABSENTE,)
    assert str(capture.value).endswith(MARCHE_A_SUIVRE_SCHEMA_PERIME)
