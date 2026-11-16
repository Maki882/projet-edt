"""Outils de l'assistant : schémas, outils de lecture, propositions sans écriture.

Données : le jeu minimal et sa version saisie à la main (`version_minimale`),
le 5 octobre 2026 (semestre S1 de 2026-2027).
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import logging

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import Niveau
from edt.domaine.enumerations import JOURS
from edt.domaine.regles import EtatRegle, NaturePortee, TypeRegle
from edt.services.assistant import outils
from edt.services.assistant.outils import OUTILS, ContexteOutil, borner, etape, executer_outil
from edt.services.assistant.propositions import NatureProposition

AUJOURDHUI = dt.date(2026, 10, 5)
MOTS_NON_PRIS_EN_CHARGE = ("minimum", "maximum", "minLength", "maxLength", "multipleOf", "pattern")


def _contexte() -> ContexteOutil:
    compteur = itertools.count(1)
    return ContexteOutil(
        reference="as-test", aujourdhui=AUJOURDHUI, numeroter=lambda: next(compteur)
    )


def _appeler(nom: str, contexte: ContexteOutil | None = None, **entree) -> dict:
    return executer_outil(nom, "toolu_test", entree, contexte or _contexte())


def _nombre_de(modele) -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(modele))


# --------------------------------------------------------------------------- #
# Schémas
# --------------------------------------------------------------------------- #


def _objets(schema):
    """Tous les sous-schémas de type objet."""
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for valeur in schema.values():
            yield from _objets(valeur)
    elif isinstance(schema, list):
        for valeur in schema:
            yield from _objets(valeur)


def _cles(schema):
    if isinstance(schema, dict):
        for cle, valeur in schema.items():
            yield cle
            if cle != "properties":
                yield from _cles(valeur)
            else:
                for sous in valeur.values():
                    yield from _cles(sous)
    elif isinstance(schema, list):
        for valeur in schema:
            yield from _cles(valeur)


def test_douze_outils_tries_par_nom_sans_mode_strict():
    noms = [outil["name"] for outil in OUTILS]
    assert len(noms) == 12 and noms == sorted(noms)
    assert set(noms) == set(outils._EXECUTANTS)
    for outil in OUTILS:
        assert "strict" not in outil and outil["description"]
        for objet in _objets(outil["input_schema"]):
            assert objet["additionalProperties"] is False, outil["name"]
            assert objet["required"] == list(objet["properties"]), outil["name"]
        assert not set(_cles(outil["input_schema"])) & set(MOTS_NON_PRIS_EN_CHARGE), outil["name"]


def test_schemas_identiques_d_une_construction_a_l_autre():
    assert json.dumps(outils._construire_outils(), sort_keys=True) == json.dumps(
        OUTILS, sort_keys=True
    )
    assert outils.OUTILS_JSON == json.dumps(
        OUTILS, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def test_enumerations_tirees_du_domaine():
    par_nom = {outil["name"]: outil["input_schema"] for outil in OUTILS}
    regle = par_nom["proposer_regle"]["properties"]
    assert regle["type"]["enum"] == [t.value for t in TypeRegle]
    assert regle["portee"]["properties"]["nature"]["enum"] == [p.value for p in NaturePortee]
    assert regle["niveau"]["anyOf"][0]["enum"] == [n.value for n in Niveau]
    jours = regle["parametres"]["properties"]["jours"]["anyOf"][0]["items"]["enum"]
    assert jours == list(JOURS)
    etats = par_nom["lister_regles"]["properties"]["etat"]["anyOf"][0]["enum"]
    assert etats == [e.value for e in EtatRegle]


def _nullable(schema: dict) -> bool:
    return any(branche.get("type") == "null" for branche in schema.get("anyOf", []))


def _valeur_plausible(schema: dict):
    """Une valeur du bon type pour ce schéma (null s'il l'accepte)."""
    if _nullable(schema):
        return None
    if "enum" in schema:
        return schema["enum"][0]
    return {
        "string": "X",
        "integer": 1,
        "array": [],
        "object": {
            nom: _valeur_plausible(sous) for nom, sous in schema.get("properties", {}).items()
        },
    }[schema["type"]]


def _type_de(schema: dict) -> str:
    if "anyOf" in schema:
        return next(b["type"] for b in schema["anyOf"] if b.get("type") != "null")
    return schema["type"]


MAUVAISES_VALEURS = {
    "string": [12, ["X"], {"x": 1}],
    "integer": ["douze", 1.5, True, [1]],
    "array": ["X", {"x": 1}, [12], [{"x": 1}], [None]],
    "object": ["X", [1], {"inconnu": 1}],
}


def _entrees_mal_formees(schema: dict, base: dict):
    """Pour chaque propriété (objets imbriqués compris) : absente, puis de chaque mauvais type."""
    for nom, sous in schema["properties"].items():
        yield nom + " absent", {k: v for k, v in base.items() if k != nom}
        for valeur in MAUVAISES_VALEURS[_type_de(sous)]:
            yield f"{nom}={valeur!r}", {**base, nom: valeur}
        simple = next((b for b in sous.get("anyOf", []) if b.get("type") != "null"), sous)
        if "enum" in simple or "enum" in simple.get("items", {}):
            inconnue = "valeur_inconnue" if "enum" in simple else ["valeur_inconnue"]
            yield f"{nom}={inconnue!r}", {**base, nom: inconnue}
        objet = sous if sous.get("type") == "object" else None
        if objet is None and _type_de(sous) == "object":
            objet = next(b for b in sous["anyOf"] if b.get("type") == "object")
        if objet is not None:
            interne = _valeur_plausible({**objet})
            for detail, valeur in _entrees_mal_formees(objet, interne):
                yield f"{nom}.{detail}", {**base, nom: valeur}


@pytest.mark.cas_limite
@pytest.mark.parametrize("outil", OUTILS, ids=[o["name"] for o in OUTILS])
def test_arguments_mal_formes_rendus_en_refus_clair(version_minimale, outil):
    """Cas : le modèle envoie un argument absent ou du mauvais type (un nombre pour un code,
    un texte pour une liste, un objet inconnu...) : les outils ne sont pas stricts.

    Risque : une exception imprévue, rendue au modèle en « erreur interne de l'outil » qu'il
    ne sait pas corriger, et une trace d'erreur dans le journal à chaque faute de forme.
    """
    schema = outil["input_schema"]
    base = _valeur_plausible(schema)
    internes = []
    for detail, entree in [("vide", {}), *_entrees_mal_formees(schema, base)]:
        resultat = _appeler(outil["name"], **entree)
        if resultat["content"].startswith("Erreur interne"):
            internes.append(detail)
    assert internes == [], f"{outil['name']} : erreur interne pour {internes}"


# --------------------------------------------------------------------------- #
# Garde-fous de l'exécution
# --------------------------------------------------------------------------- #


def test_borner_coupe_a_la_derniere_ligne_entiere():
    texte = "\n".join(f"ligne {i:04d} " + "x" * 40 for i in range(200))
    coupe = borner(texte)
    assert len(coupe) <= outils.TAILLE_MAX
    assert coupe.endswith("lignes de plus : précisez la recherche.")
    gardees = coupe.splitlines()[:-1]
    assert all(ligne in texte.splitlines() for ligne in gardees)
    assert borner("court") == "court"


def test_outil_inconnu_ou_entree_invalide_rendus_en_erreur(base):
    resultat = _appeler("supprimer_tout")
    assert resultat["is_error"] and "Outil inconnu" in resultat["content"]
    resultat = executer_outil("chercher", "toolu_x", ["pas", "un", "objet"], _contexte())
    assert resultat["is_error"] and resultat["tool_use_id"] == "toolu_x"


@pytest.mark.cas_limite
def test_exception_inattendue_d_un_outil_devient_erreur_interne_avec_reference(
    base, monkeypatch, caplog
):
    """Cas : un outil lève une exception non prévue (bogue).

    Risque : l'échange entier échoue et l'utilisateur ne sait pas quoi faire ; ou la trace
    part au modèle. Ici : erreur interne avec la référence, trace dans le journal seulement.
    """

    def en_panne(session, entree, contexte):
        raise ZeroDivisionError("division par zéro")

    monkeypatch.setitem(outils._EXECUTANTS, "lire_contexte", en_panne)
    with caplog.at_level(logging.ERROR):
        resultat = _appeler("lire_contexte")
    assert resultat["is_error"]
    assert resultat["content"] == "Erreur interne de l'outil (référence as-test)."
    assert "ZeroDivisionError" in caplog.text


@pytest.mark.cas_limite
def test_base_occupee_pendant_un_outil(base, monkeypatch):
    """Cas : un chargement d'établissement fictif tient la base pendant que l'outil lit.

    Risque : erreur interne ou échange perdu ; ici le modèle reçoit « base occupée, réessayer ».
    """

    def occupee(session, entree, contexte):
        raise OperationalError("SELECT", {}, Exception("database is locked"))

    monkeypatch.setitem(outils._EXECUTANTS, "lire_contexte", occupee)
    resultat = _appeler("lire_contexte")
    assert resultat["is_error"] and "Base momentanément occupée" in resultat["content"]


def test_etapes_affichees(base):
    assert etape("chercher", {"entite": "enseignant", "texte": "Bertin"}) == (
        "Je cherche dans les enseignants (« Bertin »)…"
    )
    assert etape("salles_libres", {"jour": "jeudi", "moment": "apres_midi"}) == (
        "Je cherche les salles libres le jeudi après-midi…"
    )
    assert etape("expliquer_regle", {"code": "R007"}) == "J'examine la règle R007…"
    assert etape("proposer_regle", {}) == "Je vérifie la proposition…"
    assert len(etape("emploi_du_temps", {"code": "x" * 300})) < 80


# --------------------------------------------------------------------------- #
# Outils de lecture
# --------------------------------------------------------------------------- #


def test_lire_contexte(version_minimale):
    resultat = _appeler("lire_contexte")
    assert not resultat.get("is_error"), resultat["content"]
    texte = resultat["content"]
    assert "Date du jour : lundi 5 octobre 2026." in texte
    assert "Année scolaire en cours : 2026-2027" in texte
    assert "- S1 « Semestre 1" in texte and "version active n° 1" in texte
    assert "semestre courant" in texte
    assert "- jeudi : matin 8 h 30 - 12 h 45, après-midi 13 h 45 - 18 h 00" in texte
    assert "Types de séance autorisés le samedi :" in texte
    assert "Calcul en cours : aucun." in texte


def test_chercher_trouve_et_suggere(jeu_minimal):
    texte = _appeler("chercher", entite="enseignant", texte="Martin", filtres=[], page=None)[
        "content"
    ]
    assert "1 enseignant trouvé" in texte and "ENS001" in texte
    texte = _appeler("chercher", entite="enseignant", texte="Martn", filtres=[], page=None)[
        "content"
    ]
    assert "Aucun élément parmi les enseignants ne correspond à « Martn »" in texte
    assert "Mme Martin (ENS001)" in texte


def test_chercher_avec_filtres_par_code(jeu_minimal):
    texte = _appeler(
        "chercher",
        entite="besoin",
        texte=None,
        filtres=[{"nom": "matiere", "valeur": "IM.1101"}, {"nom": "groupe", "valeur": "A1-G1"}],
        page=None,
    )["content"]
    assert "trouvé" in texte and "Mathématiques" in texte and "n° " in texte
    resultat = _appeler(
        "chercher",
        entite="salle",
        texte=None,
        filtres=[{"nom": "couleur", "valeur": "bleu"}],
        page=None,
    )
    assert resultat["is_error"] and "filtres : campus, type, accessibilite" in resultat["content"]


def test_salles_libres_le_jeudi_apres_midi(version_minimale):
    resultat = _appeler(
        "salles_libres",
        semestre=None,
        jour="jeudi",
        date=None,
        moment="apres_midi",
        debut=None,
        fin=None,
        capacite_min=30,
        campus=None,
        type_salle=None,
    )
    texte = resultat["content"]
    assert not resultat.get("is_error"), texte
    assert texte.startswith("Semaine type du semestre S1 (version active n° 1")
    assert "jeudi 13 h 45 - 18 h 00, au moins 30 places." in texte
    assert "- ISSY : L115 (30 places, TP)" in texte and "N105 (40 places, COURS)" in texte
    assert "Libres certaines semaines seulement :" in texte and "N016" in texte
    assert "L115 (ISSY) : indisponible (Travaux) le 05/11/2026" in texte


def test_salles_libres_refuse_un_jour_qui_ne_correspond_pas_a_la_date(version_minimale):
    resultat = _appeler(
        "salles_libres",
        semestre=None,
        jour="mardi",
        date="2026-11-05",
        moment="matin",
        debut=None,
        fin=None,
        capacite_min=None,
        campus=None,
        type_salle=None,
    )
    assert resultat["is_error"] and "est un jeudi, pas un mardi" in resultat["content"]


def test_emploi_du_temps_d_un_enseignant_avec_lien(version_minimale):
    contexte = _contexte()
    texte = _appeler(
        "emploi_du_temps",
        contexte,
        nature="enseignant",
        code="Mme Martin",
        semestre=None,
        date=None,
        jour="lundi",
    )["content"]
    assert "Emploi du temps de" in texte and "semaine type du semestre S1, lundi" in texte
    assert "- lundi 8 h 30 - 10 h 30 : CM Mathématiques ; A1 ; salle N016 (NDC)" in texte
    (lien,) = contexte.liens
    assert lien.adresse.startswith("/emploi-du-temps?") and "enseignant_id=" in lien.adresse
    assert "affichage=jour" in lien.adresse


def test_code_inconnu_rend_les_elements_proches(version_minimale):
    resultat = _appeler(
        "emploi_du_temps", nature="salle", code="N10", semestre=None, date=None, jour=None
    )
    assert resultat["is_error"]
    assert (
        "Aucune salle ne s'appelle « N10 »" in resultat["content"] and "N105" in resultat["content"]
    )


def test_lister_et_expliquer_les_regles(jeu_minimal):
    texte = _appeler(
        "lister_regles",
        etat=None,
        type=None,
        groupe="A1-G1",
        enseignant=None,
        matiere=None,
        texte=None,
    )["content"]
    assert "- R001 (active" in texte and "- R002 (active" in texte  # R001 vise la promotion A1
    contexte = _contexte()
    resultat = _appeler("expliquer_regle", contexte, code="r002")
    assert not resultat.get("is_error") and "règle R002" in resultat["content"]
    assert contexte.liens[0].adresse.endswith("q=R002")


def test_points_a_traiter(version_minimale):
    contexte = _contexte()
    resultat = _appeler("points_a_traiter", contexte, semestre="S1", gravite=None)
    assert not resultat.get("is_error"), resultat["content"]
    assert resultat["content"].startswith(
        "Semestre S1, version active n° 1 : aucun point à traiter"
    )
    assert contexte.liens[0].adresse.startswith("/a-traiter?semestre_id=")


# --------------------------------------------------------------------------- #
# Propositions
# --------------------------------------------------------------------------- #


def _proposer_regle(contexte, **changements):
    entree = {
        "type": "CRENEAUX_INTERDITS",
        "portee": {"nature": "type_seance", "cible": "CM"},
        "parametres": {
            "jours": ["mercredi"],
            "debut": "13:45",
            "fin": "18:00",
            "duree": None,
            "mode": None,
            "campus": None,
            "salles": None,
            "personnes": None,
            "unite": None,
            "maximum": None,
            "nombre": None,
            "parmi": None,
            "avant": None,
            "apres": None,
        },
        "niveau": None,
        "periode": {"semestre": None, "date_debut": None, "date_fin": None},
        "commentaire": None,
    }
    entree.update(changements)
    return _appeler("proposer_regle", contexte, **entree)


def test_proposer_regle_valide_sans_rien_ecrire(jeu_minimal):
    avant = _nombre_de(m.Regle)
    contexte = _contexte()
    resultat = _proposer_regle(contexte)
    assert not resultat.get("is_error"), resultat["content"]
    assert resultat["content"].startswith("Proposition P1 présentée à l'utilisateur")
    assert "Rien n'est enregistré" in resultat["content"]
    (proposition,) = contexte.propositions
    assert proposition.nature is NatureProposition.REGLE and proposition.code == "P1"
    brouillon = proposition.donnees["brouillon"]
    assert brouillon["annee"] == "2026-2027" and brouillon["portee"] == {
        "nature": "type_seance",
        "cible": "CM",
    }
    assert brouillon["parametres"]["jours"] == ["mercredi"]
    assert "mercredi" in proposition.phrase
    assert _nombre_de(m.Regle) == avant


def test_proposer_regle_invalide_rend_les_erreurs_par_champ(jeu_minimal):
    contexte = _contexte()
    resultat = _proposer_regle(contexte, portee={"nature": "groupe", "cible": "Z9"})
    assert resultat["is_error"] and "portee.cible" in resultat["content"]
    assert contexte.propositions == []


@pytest.mark.cas_limite
def test_quatrieme_proposition_d_un_echange_refusee(jeu_minimal):
    """Cas : le modèle enchaîne quatre propositions dans une réponse.

    Risque : un panneau rempli de cartes que l'utilisateur confirme sans les lire.
    """
    contexte = _contexte()
    for jour in ("lundi", "mardi", "mercredi"):
        resultat = _appeler(
            "proposer_regle",
            contexte,
            type="CRENEAUX_INTERDITS",
            portee={"nature": "type_seance", "cible": "CM"},
            parametres={"jours": [jour], "debut": "08:30", "fin": "10:30"},
            niveau=None,
            periode={},
            commentaire=None,
        )
        assert not resultat.get("is_error"), resultat["content"]
    resultat = _proposer_regle(contexte)
    assert resultat["is_error"] and "Pas plus de 3 propositions" in resultat["content"]
    assert [p.code for p in contexte.propositions] == ["P1", "P2", "P3"]


def test_proposer_modification_regle(jeu_minimal):
    contexte = _contexte()
    resultat = _appeler(
        "proposer_modification_regle",
        contexte,
        code="R002",
        portee=None,
        parametres={"duree": 90},
        niveau=None,
        periode=None,
        commentaire=None,
    )
    assert not resultat.get("is_error"), resultat["content"]
    (proposition,) = contexte.propositions
    assert proposition.titre == "Modifier R002" and proposition.phrase_avant
    assert proposition.phrase != proposition.phrase_avant
    assert proposition.donnees["changements"] == {"parametres": {"duree": 90}}


@pytest.mark.cas_limite
def test_argument_omis_vaut_null(version_minimale):
    """Cas : le modèle omet les paramètres non précisés au lieu d'écrire null (les outils ne
    sont pas stricts : rien ne l'oblige à tous les donner).

    Risque : un paramètre omis lu autrement que null : une autre réponse, ou une période
    omise dans une modification prise pour « toute l'année » au lieu de « inchangée ».
    """
    avec_nuls = _appeler(
        "salles_libres",
        semestre=None,
        jour="jeudi",
        date=None,
        moment="apres_midi",
        debut=None,
        fin=None,
        capacite_min=30,
        campus=None,
        type_salle=None,
    )
    omis = _appeler("salles_libres", jour="jeudi", moment="apres_midi", capacite_min=30)
    assert not omis.get("is_error") and omis["content"] == avec_nuls["content"]
    contexte = _contexte()
    resultat = _appeler(
        "proposer_modification_regle", contexte, code="R002", parametres={"duree": 90}
    )
    assert not resultat.get("is_error"), resultat["content"]
    assert contexte.propositions[0].donnees["changements"] == {"parametres": {"duree": 90}}


def test_proposer_etat_regle(jeu_minimal):
    contexte = _contexte()
    resultat = _appeler("proposer_etat_regle", contexte, code="R001", etat="inactive")
    assert not resultat.get("is_error")
    assert contexte.propositions[0].titre == "Désactiver R001"
    resultat = _appeler("proposer_etat_regle", contexte, code="R002", etat="active")
    assert resultat["is_error"] and "déjà active" in resultat["content"]
    resultat = _appeler("proposer_etat_regle", contexte, code="R003", etat="active")
    assert not resultat.get("is_error") and contexte.propositions[-1].titre == "Activer R003"


def test_proposer_indisponibilite_traduit_le_moment_par_la_grille(version_minimale):
    avant = _nombre_de(m.Indisponibilite)
    contexte = _contexte()
    resultat = _appeler(
        "proposer_indisponibilite",
        contexte,
        nature="enseignant",
        code="ENS001",
        forme="recurrente",
        jour="lundi",
        date_debut=None,
        date_fin=None,
        moment="matin",
        debut=None,
        fin=None,
        motif=None,
    )
    assert not resultat.get("is_error"), resultat["content"]
    (proposition,) = contexte.propositions
    assert proposition.phrase == "Mme Martin (ENS001), chaque lundi de 8 h 30 à 12 h 45"
    valeurs = proposition.donnees["valeurs"]
    assert (valeurs["jour"], valeurs["debut_min"], valeurs["fin_min"]) == (0, 510, 765)
    assert any("1 séance de la version active" in d for d in proposition.details)
    assert _nombre_de(m.Indisponibilite) == avant


def test_proposer_indisponibilite_datee_toute_la_journee(version_minimale):
    contexte = _contexte()
    resultat = _appeler(
        "proposer_indisponibilite",
        contexte,
        nature="salle",
        code="N016",
        forme="datee",
        jour=None,
        date_debut="2026-10-06",
        date_fin=None,
        moment="journee",
        debut=None,
        fin=None,
        motif="Réunion",
    )
    assert not resultat.get("is_error"), resultat["content"]
    proposition = contexte.propositions[0]
    assert proposition.phrase == "Salle N016 (NDC), le mardi 06/10/2026, toute la journée"
    assert "debut_min" not in proposition.donnees["valeurs"]


def test_preparer_formulaire_besoin(version_minimale):
    contexte = _contexte()
    resultat = _appeler(
        "preparer_formulaire_besoin",
        contexte,
        semestre=None,
        matiere="IM.1101",
        type_seance="TD",
        groupes=["A1-G1"],
        enseignants=None,
        duree_minutes=120,
        seances_par_semaine=1,
    )
    assert not resultat.get("is_error"), resultat["content"]
    assert "Nouveau besoin à vérifier : TD Mathématiques (IM.1101), A1-G1" in resultat["content"]
    assert "Besoins déjà présents" in resultat["content"]
    formulaire = contexte.liens[0]
    assert (
        formulaire.adresse.startswith("/besoins/nouveau?") and "duree_min=2+h" in formulaire.adresse
    )
    assert any(
        lien.adresse.startswith("/besoins/") and "nouveau" not in lien.adresse
        for lien in contexte.liens
    )


def test_emploi_du_temps_a_une_date_sans_seances_datees_lit_la_semaine_type(version_minimale):
    texte = _appeler(
        "emploi_du_temps",
        nature="groupe",
        code="A1-G1a",
        semestre=None,
        date="2026-11-02",
        jour="lundi",
    )["content"]
    assert "cette version n'a pas de séances datées : semaine type de la semaine n° 9" in texte
    assert "- lundi 8 h 30 - 10 h 30 : CM Mathématiques ; A1" in texte


@pytest.mark.cas_limite
def test_emploi_du_temps_d_une_classe_compte_ses_demi_groupes_et_ses_langues(version_minimale):
    """Cas : « la classe A1-G1 a-t-elle cours le mercredi ? » ; ce jour-là, seuls ses
    demi-groupes (TP) et le groupe de langue qui partage ses étudiants ont cours.

    Risque : l'outil ne lit que la vue restreinte et répond « 0 séance » : l'assistant
    annonce la classe libre.
    """
    texte = _appeler(
        "emploi_du_temps",
        nature="groupe",
        code="A1-G1",
        semestre=None,
        date=None,
        jour="mercredi",
    )["content"]
    assert "TP Algorithmique ; A1-G1a" in texte and "TP Algorithmique ; A1-G1b" in texte
    assert "LANGUE Anglais ; A1-ANG-B2" in texte
    assert "une partie du groupe" in texte
    promotion = _appeler(
        "emploi_du_temps", nature="groupe", code="A1", semestre=None, date=None, jour="mercredi"
    )["content"]
    assert "TP Algorithmique ; A1-G1a" in promotion and ": 0 séance." not in promotion
