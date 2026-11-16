"""Outils exposés au modèle : schémas stricts et exécution.

Douze outils, triés par nom, construits une fois à l'import (`OUTILS`). Les
schémas suivent le mode strict de l'API : `additionalProperties: false` sur
chaque objet, toutes les propriétés requises (une valeur facultative s'écrit
`anyOf` avec `null`), aucun mot-clé de borne (`minimum`, `maxLength`...) :
les bornes sont contrôlées par les services. Les énumérations viennent du
domaine (types et états de règle, portées, niveaux, jours).

Aucun outil n'écrit. Les outils de lecture appellent les services et
rendent un texte français compact (`TAILLE_MAX` caractères au plus) ; les
outils `proposer_*` valident par le service concerné, puis ajoutent une
`Proposition` à l'échange (`ContexteOutil.ajouter`) : l'écriture n'a lieu
qu'au clic sur « Confirmer » (`edt.services.assistant.propositions`).

Chaque exécution ouvre sa propre session courte, en lecture. Une erreur
métier (`ErreurEDT`) revient au modèle comme `tool_result` en erreur, avec
le message du service ; une base occupée aussi ; une exception inattendue
est journalisée avec sa trace et revient comme « erreur interne de l'outil »
avec la référence de l'échange.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import logging
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session, selectinload

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.calendrier import semaine_de, semaines_entre
from edt.domaine.contraintes import Niveau
from edt.domaine.enumerations import JOURS, DemiJournee, Gravite
from edt.domaine.heures import duree_vers_texte, minutes_vers_texte, texte_vers_minutes
from edt.domaine.parametres import DEJEUNER_FIXE, DEJEUNER_LIBRE
from edt.domaine.regles import CATALOGUE_TYPES, EtatRegle, NaturePortee, TypeRegle
from edt.erreurs import ErreurDonneesInvalides, ErreurEDT, ErreurIntrouvable
from edt.services import regles
from edt.services.assistant.propositions import Lien, NatureProposition, Proposition
from edt.services.calendrier import semestre_par_defaut
from edt.services.emploi_du_temps import anomalies, occupation, presentation, versions, vues
from edt.services.emploi_du_temps.presentation import adresse_regle, plages_de_semaines
from edt.services.jeux import operation_en_cours
from edt.services.parametres import lire_parametres
from edt.services.referentiels import operations
from edt.services.referentiels.conversion import texte_valeur

journal = logging.getLogger(__name__)

TAILLE_MAX = 4000
"""Caractères au plus par résultat d'outil ; au-delà, coupure à la dernière ligne entière."""
TAILLE_PAGE = 25
REGLES_MAX = 40
SEANCES_MAX = 60
PROPOSITIONS_MAX = 3
"""Propositions au plus par échange."""

ENTITES_CHERCHABLES = (
    "salle",
    "enseignant",
    "groupe",
    "matiere",
    "besoin",
    "campus",
    "type_seance",
    "type_salle",
    "equipement",
    "indisponibilite",
    "semestre",
)


# --------------------------------------------------------------------------- #
# Schémas
# --------------------------------------------------------------------------- #


def _nul(schema: dict[str, Any]) -> dict[str, Any]:
    """Valeur facultative : le schéma ou null (la clé reste dans `required`)."""
    return {"anyOf": [schema, {"type": "null"}]}


def _objet(proprietes: dict[str, Any], description: str | None = None) -> dict[str, Any]:
    objet: dict[str, Any] = {
        "type": "object",
        "properties": proprietes,
        "required": list(proprietes),
        "additionalProperties": False,
    }
    if description:
        objet["description"] = description
    return objet


_TEXTE = {"type": "string"}
_ENTIER = {"type": "integer"}
_DATE = {"type": "string", "format": "date"}
_HEURE = {"type": "string", "description": "Heure « HH:MM » (24 h)."}
_JOUR = {"type": "string", "enum": list(JOURS)}
_MOMENT = {"type": "string", "enum": list(occupation.MOMENTS)}


def _valeurs_de_champ(nom: str) -> list[str]:
    return sorted(
        {
            valeur
            for definition in CATALOGUE_TYPES.values()
            for champ in definition.champs
            if champ.nom == nom
            for valeur, _ in champ.choix
        }
    )


_PARAMETRES = _objet(
    {
        "jours": _nul({"type": "array", "items": _JOUR}),
        "debut": _nul(_HEURE),
        "fin": _nul(_HEURE),
        "duree": _nul({"type": "integer", "description": "Minutes."}),
        "mode": _nul({"type": "string", "enum": _valeurs_de_champ("mode")}),
        "campus": _nul({"type": "array", "items": _TEXTE, "description": "Codes de campus."}),
        "salles": _nul({"type": "array", "items": _TEXTE, "description": "Codes de salles."}),
        "personnes": _nul({"type": "string", "enum": _valeurs_de_champ("personnes")}),
        "unite": _nul({"type": "string", "enum": _valeurs_de_champ("unite")}),
        "maximum": _nul(_ENTIER),
        "nombre": _nul(_ENTIER),
        "parmi": _nul(
            {
                "type": "array",
                "items": _TEXTE,
                "description": "« mercredi » ou « mercredi après-midi ».",
            }
        ),
        "avant": _nul({"type": "string", "description": "Code de type de séance."}),
        "apres": _nul({"type": "string", "description": "Code de type de séance."}),
    },
    "Paramètres du type (catalogue des consignes) ; null pour un paramètre sans objet ou "
    "laissé à sa valeur par défaut.",
)
_PORTEE = _objet(
    {
        "nature": {"type": "string", "enum": [p.value for p in NaturePortee]},
        "cible": _nul(
            {
                "type": "string",
                "description": "Code de la cible (groupe, identifiant d'enseignant, campus, type "
                "de séance, matière) ; pour un besoin, « matière / type / groupes » ou son "
                "numéro ; null pour l'établissement.",
            }
        ),
    }
)
_PERIODE = _objet(
    {
        "semestre": _nul({"type": "string", "description": "Code du semestre (« S1 »)."}),
        "date_debut": _nul(_DATE),
        "date_fin": _nul(_DATE),
    },
    "Toute l'année : les trois à null.",
)
_NIVEAU = {"type": "string", "enum": [n.value for n in Niveau]}


def _construire_outils() -> list[dict[str, Any]]:
    outils = [
        {
            "name": "chercher",
            "description": "Cherche des éléments des données par texte et filtres (25 au plus par "
            "page). À appeler pour retrouver le code exact d'une salle, d'un enseignant, d'un "
            "groupe, d'une matière ou d'un besoin avant de répondre ou de proposer, et pour une "
            "question de liste (« quels enseignants vacataires ? »). Filtres : salle (campus, "
            "type, accessibilite), enseignant (statut), groupe (nature, parent), besoin "
            "(semestre, groupe, matiere, type, enseignant), indisponibilite (cible, forme).",
            "input_schema": _objet(
                {
                    "entite": {"type": "string", "enum": list(ENTITES_CHERCHABLES)},
                    "texte": _nul(_TEXTE),
                    "filtres": {
                        "type": "array",
                        "items": _objet({"nom": _TEXTE, "valeur": _TEXTE}),
                    },
                    "page": _nul(_ENTIER),
                }
            ),
        },
        {
            "name": "emploi_du_temps",
            "description": "Séances d'un groupe, d'un enseignant ou d'une salle dans la version "
            "active d'un semestre : semaine type (date null) ou semaine datée qui contient la "
            "date ; jour pour se limiter à un jour. À appeler pour toute question sur les cours "
            "de quelqu'un ou l'occupation d'une salle.",
            "input_schema": _objet(
                {
                    "nature": {"type": "string", "enum": ["groupe", "enseignant", "salle"]},
                    "code": _TEXTE,
                    "semestre": _nul(_TEXTE),
                    "date": _nul(_DATE),
                    "jour": _nul(_JOUR),
                }
            ),
        },
        {
            "name": "expliquer_regle",
            "description": "Explique une règle personnalisée par son code (« R007 ») : phrase, "
            "niveau, état, période, séances et personnes concernées par semestre, réglage "
            "remplacé.",
            "input_schema": _objet({"code": _TEXTE}),
        },
        {
            "name": "lire_contexte",
            "description": "Contexte de l'établissement : année scolaire en cours, semestres "
            "(dates, version active, nombre de séances), réglages (ouverture, jours travaillés, "
            "déjeuner, pause), demi-journées de la grille par jour, calcul en cours, nombre "
            "d'éléments par catégorie. À appeler en premier dès qu'une réponse dépend de l'année, "
            "du semestre, des horaires ou de la version active.",
            "input_schema": _objet({}),
        },
        {
            "name": "lister_regles",
            "description": "Règles personnalisées de l'année en cours avec leur phrase, filtrées. "
            "À appeler avant de proposer une règle (doublon, règle existante à modifier) et pour "
            "toute question sur les règles.",
            "input_schema": _objet(
                {
                    "etat": _nul({"type": "string", "enum": [e.value for e in EtatRegle]}),
                    "type": _nul({"type": "string", "enum": [t.value for t in TypeRegle]}),
                    "groupe": _nul(_TEXTE),
                    "enseignant": _nul(_TEXTE),
                    "matiere": _nul(_TEXTE),
                    "texte": _nul(_TEXTE),
                }
            ),
        },
        {
            "name": "points_a_traiter",
            "description": "Points à traiter de la version active d'un semestre : erreurs et "
            "avertissements par règle, premiers messages, séances à programmer. À appeler "
            "pour « y a-t-il des problèmes ? » ou « pourquoi ce conflit ? ».",
            "input_schema": _objet(
                {
                    "semestre": _nul(_TEXTE),
                    "gravite": _nul(
                        {"type": "string", "enum": ["erreur", "avertissement", "information"]}
                    ),
                }
            ),
        },
        {
            "name": "preparer_formulaire_besoin",
            "description": "Prépare le formulaire prérempli d'un nouveau besoin (enseignement à "
            "placer) et liste les besoins voisins déjà présents (même matière, mêmes groupes). "
            "N'enregistre rien : l'utilisateur vérifie et enregistre lui-même. À appeler pour "
            "« ajoute 2 h de TD de ... à ... ».",
            "input_schema": _objet(
                {
                    "semestre": _nul(_TEXTE),
                    "matiere": _TEXTE,
                    "type_seance": _TEXTE,
                    "groupes": {"type": "array", "items": _TEXTE},
                    "enseignants": _nul({"type": "array", "items": _TEXTE}),
                    "duree_minutes": _ENTIER,
                    "seances_par_semaine": _ENTIER,
                }
            ),
        },
        {
            "name": "proposer_etat_regle",
            "description": "Propose d'activer (confirmer une règle proposée) ou de désactiver une "
            "règle existante. N'enregistre rien : l'utilisateur confirme dans le panneau.",
            "input_schema": _objet(
                {
                    "code": _TEXTE,
                    "etat": {
                        "type": "string",
                        "enum": [EtatRegle.ACTIVE.value, EtatRegle.INACTIVE.value],
                    },
                }
            ),
        },
        {
            "name": "proposer_indisponibilite",
            "description": "Propose d'enregistrer une indisponibilité (absence d'un enseignant, "
            "d'une salle ou d'un groupe) : chaque semaine (forme recurrente, avec jour) ou sur "
            "une période (forme datee, avec dates). Horaire : moment (matin, apres_midi, journee) "
            "ou debut et fin. N'enregistre rien : l'utilisateur confirme dans le panneau.",
            "input_schema": _objet(
                {
                    "nature": {"type": "string", "enum": ["enseignant", "salle", "groupe"]},
                    "code": _TEXTE,
                    "forme": {"type": "string", "enum": ["recurrente", "datee"]},
                    "jour": _nul(_JOUR),
                    "date_debut": _nul(_DATE),
                    "date_fin": _nul(_DATE),
                    "moment": _nul(_MOMENT),
                    "debut": _nul(_HEURE),
                    "fin": _nul(_HEURE),
                    "motif": _nul(_TEXTE),
                }
            ),
        },
        {
            "name": "proposer_modification_regle",
            "description": "Propose de modifier une règle existante (code « R007 ») : seuls les "
            "champs non null changent ; chaque paramètre donné remplace l'ancien. N'enregistre "
            "rien : l'utilisateur confirme dans le panneau.",
            "input_schema": _objet(
                {
                    "code": _TEXTE,
                    "portee": _nul(_PORTEE),
                    "parametres": _nul(_PARAMETRES),
                    "niveau": _nul(_NIVEAU),
                    "periode": _nul(_PERIODE),
                    "commentaire": _nul(_TEXTE),
                }
            ),
        },
        {
            "name": "proposer_regle",
            "description": "Propose une nouvelle règle personnalisée pour l'année en cours ; "
            "l'application la valide (phrase générée, ou erreurs par champ). N'enregistre rien : "
            "l'utilisateur confirme dans le panneau. Types, portées et paramètres : catalogue des "
            "consignes.",
            "input_schema": _objet(
                {
                    "type": {"type": "string", "enum": [t.value for t in TypeRegle]},
                    "portee": _PORTEE,
                    "parametres": _PARAMETRES,
                    "niveau": _nul(_NIVEAU),
                    "periode": _PERIODE,
                    "commentaire": _nul(_TEXTE),
                }
            ),
        },
        {
            "name": "salles_libres",
            "description": "Salles libres sur un créneau de la version active : un jour de la "
            "semaine type (jour) ou une date (date) ; horaire par moment (matin, apres_midi, "
            "journee, selon la grille) ou par debut et fin ; filtres capacité minimale, campus, "
            "type de salle. Renvoie les salles libres toutes les semaines, celles libres "
            "certaines semaines seulement et les exceptions datées (indisponibilités).",
            "input_schema": _objet(
                {
                    "semestre": _nul(_TEXTE),
                    "jour": _nul(_JOUR),
                    "date": _nul(_DATE),
                    "moment": _nul(_MOMENT),
                    "debut": _nul(_HEURE),
                    "fin": _nul(_HEURE),
                    "capacite_min": _nul(_ENTIER),
                    "campus": _nul(_TEXTE),
                    "type_salle": _nul(_TEXTE),
                }
            ),
        },
    ]
    for outil in outils:
        outil["strict"] = True
    return sorted(outils, key=lambda outil: outil["name"])


OUTILS: list[dict[str, Any]] = _construire_outils()
"""Définitions envoyées au modèle, identiques d'un appel à l'autre (préfixe en cache)."""

OUTILS_JSON = json.dumps(OUTILS, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
"""Forme compacte et stable des définitions (empreinte et estimation de taille)."""


# --------------------------------------------------------------------------- #
# Contexte et résultat
# --------------------------------------------------------------------------- #


@dataclass
class ContexteOutil:
    """Ce que les outils d'un échange partagent.

    `reference` : référence de l'échange (« as-<jeton> ») citée dans les
    erreurs internes ; `numeroter` : donne le numéro de la proposition
    suivante dans la conversation (P1, P2...) ; `propositions` et `liens` :
    ce que l'échange affichera, ajouté par le serveur, jamais écrit par le
    modèle. L'année en cours est résolue une fois, au premier outil qui en a
    besoin.
    """

    reference: str
    aujourdhui: dt.date
    numeroter: Callable[[], int]
    propositions: list[Proposition] = field(default_factory=list)
    liens: list[Lien] = field(default_factory=list)
    _annee: tuple[int, str] | None = None

    def annee(self, session: Session) -> tuple[int, str]:
        """Identifiant et libellé de l'année scolaire en cours (celle du semestre par défaut)."""
        if self._annee is None:
            semestre = semestre_par_defaut(session, self.aujourdhui)
            self._annee = (semestre.annee_scolaire_id, semestre.annee.libelle)
        return self._annee

    def ajouter(self, proposition_sans_numero: Callable[[int], Proposition]) -> Proposition:
        """Ajoute une proposition à l'échange ; `ErreurDonneesInvalides` au-delà de la limite."""
        if len(self.propositions) >= PROPOSITIONS_MAX:
            raise ErreurDonneesInvalides(
                f"Pas plus de {PROPOSITIONS_MAX} propositions par réponse : présentez celles-ci, "
                "l'utilisateur demandera la suite."
            )
        proposition = proposition_sans_numero(self.numeroter())
        self.propositions.append(proposition)
        journal.info("Proposition %s présentée : %s", proposition.code, proposition.resume())
        return proposition

    def lier(self, libelle: str, adresse: str) -> None:
        """Ajoute un lien de l'application sous la réponse (une seule fois par adresse)."""
        if all(lien.adresse != adresse for lien in self.liens):
            self.liens.append(Lien(libelle, adresse))


def borner(texte: str, taille: int = TAILLE_MAX) -> str:
    """Coupe un texte trop long à la dernière ligne entière, en disant combien de lignes manquent."""
    if len(texte) <= taille:
        return texte
    lignes = texte.splitlines()
    gardees: list[str] = []
    longueur = 0
    reserve = 80
    for ligne in lignes:
        if longueur + len(ligne) + 1 > taille - reserve:
            break
        gardees.append(ligne)
        longueur += len(ligne) + 1
    manquantes = len(lignes) - len(gardees)
    return "\n".join(gardees) + f"\n… {manquantes} lignes de plus : précisez la recherche."


def etape(nom: str, entree: Mapping[str, Any]) -> str:
    """Phrase d'attente affichée pendant l'outil (arguments abrégés à 40 caractères)."""

    def court(valeur: object) -> str:
        texte = str(valeur or "").strip()
        return texte if len(texte) <= 40 else texte[:39] + "…"

    if nom == "lire_contexte":
        return "Je consulte le calendrier et les réglages…"
    if nom == "chercher":
        try:
            pluriel = operations.entite(str(entree.get("entite"))).pluriel
        except ErreurEDT:
            pluriel = "données"
        texte = court(entree.get("texte"))
        return f"Je cherche dans les {pluriel}" + (f" (« {texte} »)…" if texte else "…")
    if nom == "salles_libres":
        quand = court(entree.get("date") or entree.get("jour") or "")
        moment = {"matin": " matin", "apres_midi": " après-midi", "journee": ""}.get(
            str(entree.get("moment")), ""
        )
        return f"Je cherche les salles libres{' le ' + quand if quand else ''}{moment}…"
    if nom == "emploi_du_temps":
        return f"Je consulte l'emploi du temps de {court(entree.get('code'))}…"
    if nom == "points_a_traiter":
        return "Je consulte les points à traiter…"
    if nom == "lister_regles":
        return "Je consulte les règles personnalisées…"
    if nom == "expliquer_regle":
        return f"J'examine la règle {court(entree.get('code'))}…"
    if nom == "preparer_formulaire_besoin":
        return "Je prépare le formulaire du besoin…"
    if nom.startswith("proposer_"):
        return "Je vérifie la proposition…"
    return "Je consulte les données…"


def executer_outil(nom: str, identifiant: str, entree: object, contexte: ContexteOutil) -> dict:
    """Exécute un appel d'outil ; renvoie le bloc `tool_result` (toujours, jamais d'exception)."""
    debut = time.monotonic()
    erreur = True
    try:
        executant = _EXECUTANTS.get(nom)
        if executant is None:
            texte = f"Outil inconnu : « {nom} »."
        elif not isinstance(entree, Mapping):
            texte = "Entrée invalide : un objet est attendu."
        else:
            with session_base() as session:
                texte = executant(session, entree, contexte)
            erreur = False
    except ErreurEDT as refus:
        texte = _message_erreur(refus)
        journal.info("Outil %s : refus métier : %s", nom, texte)
    except DatabaseError as panne:
        cause = str(panne.orig or panne).lower()
        if operation_en_cours() or "locked" in cause:
            journal.warning("Outil %s : base occupée (%s)", nom, panne.orig)
            texte = (
                "Base momentanément occupée (chargement de données ou calcul) : dites à "
                "l'utilisateur de réessayer dans quelques secondes."
            )
        else:
            journal.exception("Outil %s : base indisponible", nom)
            texte = f"Erreur interne de l'outil (référence {contexte.reference})."
    except Exception:
        journal.exception("Outil %s : erreur inattendue", nom)
        texte = f"Erreur interne de l'outil (référence {contexte.reference})."
    texte = borner(texte)
    journal.info(
        "Outil %s : %s en %.2f s (%d caractères)",
        nom,
        "erreur" if erreur else "réussi",
        time.monotonic() - debut,
        len(texte),
    )
    resultat: dict[str, Any] = {"type": "tool_result", "tool_use_id": identifiant, "content": texte}
    if erreur:
        resultat["is_error"] = True
    return resultat


def _message_erreur(refus: ErreurEDT) -> str:
    """Le message d'une erreur métier, une ligne par champ fautif."""
    lignes = [refus.message]
    if isinstance(refus, regles.ErreurSaisie):
        lignes = [refus.message] + [
            f"- {champ} : {texte}" for champ, texte in refus.erreurs.items()
        ]
    elif refus.details:
        lignes += [f"- {detail}" for detail in refus.details]
    return "\n".join(lignes)


# --------------------------------------------------------------------------- #
# Résolution des codes
# --------------------------------------------------------------------------- #


def _sans_accent(texte: str) -> str:
    decompose = unicodedata.normalize("NFD", str(texte).strip().casefold())
    return "".join(c for c in decompose if not unicodedata.combining(c))


def _suggestions(texte: str, candidats: Mapping[str, str], nombre: int = 5) -> list[str]:
    """Éléments proches de `texte` : clés comparées sans casse ni accent, libellés rendus."""
    index: dict[str, str] = {}
    for cle, libelle in candidats.items():
        index.setdefault(_sans_accent(cle), libelle)
    proches = difflib.get_close_matches(_sans_accent(texte), list(index), n=nombre, cutoff=0.5)
    return list(dict.fromkeys(index[p] for p in proches))


def _introuvable(debut: str, texte: str, candidats: Mapping[str, str]) -> ErreurIntrouvable:
    """« Aucune salle ne s'appelle « X ». Éléments proches : ... » (`debut` : début de phrase)."""
    proches = _suggestions(texte, candidats)
    suite = f" Éléments proches : {', '.join(proches)}." if proches else " Aucun élément proche."
    return ErreurIntrouvable(f"{debut} « {texte} ».{suite}")


def _texte_requis(entree: Mapping[str, Any], cle: str) -> str:
    valeur = entree.get(cle)
    if not isinstance(valeur, str) or not valeur.strip():
        raise ErreurDonneesInvalides(f"« {cle} » est attendu (texte non vide).")
    return valeur.strip()


def _texte(entree: Mapping[str, Any], cle: str) -> str | None:
    valeur = entree.get(cle)
    if valeur is None:
        return None
    if not isinstance(valeur, str):
        raise ErreurDonneesInvalides(f"« {cle} » doit être un texte ou null.")
    return valeur.strip() or None


def _entier(entree: Mapping[str, Any], cle: str) -> int | None:
    valeur = entree.get(cle)
    if valeur is None:
        return None
    if isinstance(valeur, bool) or not isinstance(valeur, int):
        raise ErreurDonneesInvalides(f"« {cle} » doit être un nombre entier ou null.")
    return valeur


def _date(entree: Mapping[str, Any], cle: str) -> dt.date | None:
    valeur = _texte(entree, cle)
    if valeur is None:
        return None
    try:
        return dt.date.fromisoformat(valeur)
    except ValueError:
        raise ErreurDonneesInvalides(
            f"Date invalide pour « {cle} » : « {valeur} » (AAAA-MM-JJ)."
        ) from None


def _jour(entree: Mapping[str, Any], cle: str = "jour") -> int | None:
    valeur = _texte(entree, cle)
    if valeur is None:
        return None
    for numero, nom in enumerate(JOURS):
        if _sans_accent(nom) == _sans_accent(valeur):
            return numero
    raise ErreurDonneesInvalides(f"Jour inconnu : « {valeur} » ; attendu : {', '.join(JOURS)}.")


def _heure(entree: Mapping[str, Any], cle: str) -> int | None:
    valeur = _texte(entree, cle)
    return None if valeur is None else texte_vers_minutes(valeur)


def _semestre(session: Session, contexte: ContexteOutil, code: str | None) -> m.Semestre:
    """Le semestre `code` de l'année en cours, ou le semestre courant."""
    if code is None:
        return semestre_par_defaut(session, contexte.aujourdhui)
    annee_id, libelle = contexte.annee(session)
    semestres = list(
        session.scalars(
            select(m.Semestre)
            .where(m.Semestre.annee_scolaire_id == annee_id)
            .order_by(m.Semestre.date_debut)
        )
    )
    for semestre in semestres:
        if semestre.code.casefold() == code.casefold():
            return semestre
    raise ErreurIntrouvable(
        f"Semestre « {code} » inconnu en {libelle} ; semestres : "
        + (", ".join(s.code for s in semestres) or "aucun")
        + "."
    )


def _groupe(session: Session, contexte: ContexteOutil, code: str) -> m.Groupe:
    annee_id, libelle = contexte.annee(session)
    groupes = list(session.scalars(select(m.Groupe).where(m.Groupe.annee_scolaire_id == annee_id)))
    for groupe in groupes:
        if groupe.code.casefold() == code.casefold():
            return groupe
    raise _introuvable(
        f"Aucun groupe de {libelle} ne s'appelle", code, {g.code: g.code for g in groupes}
    )


def _enseignant(session: Session, texte: str) -> m.Enseignant:
    """Par identifiant (« E0013 »), sinon par nom unique, sans casse ni accent ni civilité."""
    enseignants = list(session.scalars(select(m.Enseignant).order_by(m.Enseignant.nom)))
    for enseignant in enseignants:
        if enseignant.identifiant.casefold() == texte.casefold():
            return enseignant
    cherche = _sans_accent(texte)
    for civilite in ("mme ", "m. ", "mlle ", "m "):
        cherche = cherche.removeprefix(civilite)
    memes = [
        e
        for e in enseignants
        if cherche
        in (
            _sans_accent(e.nom),
            _sans_accent(f"{e.prenom or ''} {e.nom}"),
            _sans_accent(e.nom_affiche),
        )
    ]
    if len(memes) == 1:
        return memes[0]
    if len(memes) > 1:
        raise ErreurDonneesInvalides(
            f"Plusieurs enseignants répondent à « {texte} » : "
            + ", ".join(f"{e.nom_affiche} ({e.identifiant})" for e in memes)
            + ". Demandez lequel et donnez son identifiant."
        )
    candidats = {e.nom: f"{e.nom_affiche} ({e.identifiant})" for e in enseignants}
    candidats.update({e.identifiant: f"{e.nom_affiche} ({e.identifiant})" for e in enseignants})
    raise _introuvable("Aucun enseignant ne s'appelle", texte, candidats)


def _salles(session: Session, texte: str) -> list[m.Salle]:
    """Les salles de ce code (homonymes compris) ; « N105 (ISSY) » précise le campus."""
    code, _, reste = texte.partition("(")
    campus = reste.rstrip(")").strip() or None
    salles = list(
        session.scalars(
            select(m.Salle).options(selectinload(m.Salle.campus)).order_by(m.Salle.code)
        )
    )
    trouvees = [
        s
        for s in salles
        if s.code.casefold() == code.strip().casefold()
        and (campus is None or s.campus.code.casefold() == campus.casefold())
    ]
    if not trouvees:
        raise _introuvable("Aucune salle ne s'appelle", texte, {s.code: s.code for s in salles})
    return trouvees


def _matiere(session: Session, contexte: ContexteOutil, code: str) -> m.Matiere:
    annee_id, libelle = contexte.annee(session)
    matieres = list(session.scalars(select(m.Matiere).options(selectinload(m.Matiere.promotion))))
    de_l_annee = [
        x for x in matieres if x.promotion_id is None or x.promotion.annee_scolaire_id == annee_id
    ]
    memes = [x for x in de_l_annee if x.code.casefold() == code.casefold()]
    if len(memes) == 1:
        return memes[0]
    if len(memes) > 1:
        raise ErreurDonneesInvalides(
            f"Plusieurs matières ont le code « {code} » en {libelle} : préciser la promotion."
        )
    candidats = {x.code: f"{x.code} ({x.libelle})" for x in de_l_annee}
    candidats.update({x.libelle: f"{x.code} ({x.libelle})" for x in de_l_annee})
    raise _introuvable(f"Aucune matière de {libelle} n'a le code", code, candidats)


def _type_seance(session: Session, code: str) -> m.TypeSeance:
    types = list(session.scalars(select(m.TypeSeance).order_by(m.TypeSeance.code)))
    for type_ in types:
        if type_.code.casefold() == code.casefold():
            return type_
    raise _introuvable("Aucun type de séance n'a le code", code, {t.code: t.code for t in types})


# --------------------------------------------------------------------------- #
# Mise en forme
# --------------------------------------------------------------------------- #


def _plage(debut: int, fin: int) -> str:
    return f"{minutes_vers_texte(debut)} - {minutes_vers_texte(fin)}"


def _valeur(valeur: object) -> str:
    if isinstance(valeur, bool):
        return "oui" if valeur else "non"
    if isinstance(valeur, list | tuple):
        return ", ".join(str(v) for v in valeur if v not in (None, ""))
    return "" if valeur is None else str(valeur)


def _nombre(n: int, singulier: str, pluriel: str | None = None) -> str:
    return presentation.pluriel(n, singulier, pluriel)


# --------------------------------------------------------------------------- #
# Outils de lecture
# --------------------------------------------------------------------------- #


def _lire_contexte(session: Session, entree: Mapping[str, Any], contexte: ContexteOutil) -> str:
    from edt.services.etat import compter_entites
    from edt.services.generation import calcul_en_cours

    annee_id, libelle = contexte.annee(session)
    annee = session.get(m.AnneeScolaire, annee_id)
    courant = semestre_par_defaut(session, contexte.aujourdhui)
    lignes = [
        f"Date du jour : {presentation.date_longue(contexte.aujourdhui)}.",
        f"Année scolaire en cours : {libelle} (du {annee.date_debut:%d/%m/%Y} au "
        f"{annee.date_fin:%d/%m/%Y}).",
        "Semestres :",
    ]
    for resume in versions.lister_semestres(session):
        semestre = session.get(m.Semestre, resume.id)
        if semestre.annee_scolaire_id != annee_id:
            continue
        texte = (
            f"- {resume.code} « {resume.libelle} », du {resume.date_debut:%d/%m/%Y} au "
            f"{resume.date_fin:%d/%m/%Y} : "
        )
        active = versions.version_active(session, resume.id)
        if active is None:
            texte += "aucune version active"
        else:
            texte += (
                f"version active n° {active.numero} ({_nombre(active.nb_seances, 'séance')} par "
                f"semaine, {_nombre(active.nb_occurrences, 'séance datée', 'séances datées')})"
            )
        if resume.id == courant.id:
            texte += " ; semestre courant"
        lignes.append(texte)
    p = lire_parametres(session)
    lignes.append(
        f"Horaires : ouverture {minutes_vers_texte(p.ouverture_min)}, fermeture "
        f"{minutes_vers_texte(p.fermeture_min)} ; jours travaillés : "
        + ", ".join(JOURS[j] for j in p.jours_travailles)
        + "."
    )
    if p.dejeuner_mode == DEJEUNER_FIXE:
        dejeuner = (
            f"horaires déterminés, de {minutes_vers_texte(p.dejeuner_debut_min)} à "
            f"{minutes_vers_texte(p.dejeuner_fin_min)}"
        )
    elif p.dejeuner_mode == DEJEUNER_LIBRE:
        dejeuner = (
            f"pause libre d'au moins {duree_vers_texte(p.dejeuner_duree_min)} entre "
            f"{minutes_vers_texte(p.dejeuner_debut_min)} et {minutes_vers_texte(p.dejeuner_fin_min)}"
        )
    else:
        dejeuner = "pas de pause réservée"
    lignes.append(f"Déjeuner : {dejeuner}. Pause minimale entre deux séances : {p.pause_min} min.")
    samedi = sorted(session.scalars(select(m.TypeSeance.code).where(m.TypeSeance.samedi_autorise)))
    lignes.append(
        "Types de séance autorisés le samedi : " + (", ".join(samedi) if samedi else "aucun") + "."
    )
    lignes.append("Demi-journées de la grille :")
    creneaux = session.execute(
        select(m.Creneau.jour, m.Creneau.demi_journee, m.Creneau.debut_min, m.Creneau.fin_min)
    ).all()
    for jour in sorted({c.jour for c in creneaux}):
        morceaux = []
        for demi, nom in ((DemiJournee.MATIN, "matin"), (DemiJournee.APRES_MIDI, "après-midi")):
            bornes = [
                (c.debut_min, c.fin_min)
                for c in creneaux
                if c.jour == jour and c.demi_journee == demi
            ]
            if bornes:
                morceaux.append(
                    f"{nom} {_plage(min(b[0] for b in bornes), max(b[1] for b in bornes))}"
                )
        lignes.append(f"- {JOURS[jour]} : {', '.join(morceaux)}")
    suivi = calcul_en_cours()
    lignes.append(
        "Calcul en cours : " + (f"calcul n° {suivi.id}." if suivi is not None else "aucun.")
    )
    comptes = compter_entites(session)
    lignes.append("Données : " + ", ".join(f"{nom} {n}" for nom, n in comptes.items()) + ".")
    return "\n".join(lignes)


def _filtres_lus(
    session: Session, code: str, filtres: object, contexte: ContexteOutil
) -> dict[str, str]:
    """Filtres de `chercher` : valeur reconnue par sa clé, son libellé ou le code de l'élément."""
    if not isinstance(filtres, list):
        raise ErreurDonneesInvalides("« filtres » doit être une liste de { nom, valeur }.")
    description = operations.entite(code)
    resultat: dict[str, str] = {}
    for filtre in filtres:
        if not isinstance(filtre, Mapping):
            raise ErreurDonneesInvalides("Chaque filtre est un objet { nom, valeur }.")
        nom, valeur = str(filtre.get("nom") or ""), str(filtre.get("valeur") or "").strip()
        if description.filtre(nom) is None:
            noms = ", ".join(f.nom for f in description.filtres) or "aucun"
            raise ErreurDonneesInvalides(
                f"Filtre inconnu pour les {description.pluriel} : « {nom} » (filtres : {noms})."
            )
        if nom == "semestre" and valeur:
            resultat[nom] = str(_semestre(session, contexte, valeur).id)
            continue
        options = description.filtre(nom).options(session)
        resultat[nom] = _option(nom, valeur, options, _par_code(session, description, nom, valeur))
    return resultat


def _par_code(session: Session, description, nom: str, valeur: str) -> str | None:
    """Identifiant de l'élément de code (ou d'identifiant) `valeur` visé par le filtre, s'il y en a un."""
    filtre = description.filtre(nom)
    champ = description._index.get(filtre.champ) if filtre.champ else None
    reference = champ.reference if champ is not None else None
    if reference is None:
        return None
    modele = reference.modele
    colonne = getattr(modele, "code", None) or getattr(modele, "identifiant", None)
    if colonne is None:
        return None
    trouves = [
        ident
        for ident, code in session.execute(select(modele.id, colonne))
        if str(code).casefold() == valeur.casefold()
    ]
    return str(trouves[0]) if len(trouves) == 1 else None


def _option(nom: str, valeur: str, options: list[tuple[str, str]], par_code: str | None) -> str:
    cherche = _sans_accent(valeur)
    if not cherche:
        return ""
    for cle, _ in options:
        if cle in (valeur, par_code):
            return cle
    trouvees = [
        cle
        for cle, libelle in options
        if _sans_accent(libelle) == cherche
        or _sans_accent(libelle).startswith(cherche + " ")
        or f"({cherche})" in _sans_accent(libelle)
    ]
    if len(set(trouvees)) == 1:
        return trouvees[0]
    exemples = ", ".join(libelle for _, libelle in options[:10])
    if trouvees:
        raise ErreurDonneesInvalides(
            f"Filtre « {nom} » : « {valeur} » est ambigu ; chercher plutôt par texte."
        )
    raise ErreurDonneesInvalides(
        f"Filtre « {nom} » : valeur inconnue « {valeur} » ; valeurs possibles : {exemples}"
        + (" …" if len(options) > 10 else "")
        + "."
    )


def _chercher(session: Session, entree: Mapping[str, Any], contexte: ContexteOutil) -> str:
    code = _texte_requis(entree, "entite")
    if code not in ENTITES_CHERCHABLES:
        raise ErreurDonneesInvalides(
            f"Entité inconnue : « {code} » ; entités : {', '.join(ENTITES_CHERCHABLES)}."
        )
    description = operations.entite(code)
    texte = _texte(entree, "texte") or ""
    page = _entier(entree, "page") or 1
    filtres = _filtres_lus(session, code, entree.get("filtres") or [], contexte)
    resultat = operations.lister(
        session,
        description,
        recherche=texte,
        filtres=filtres,
        page=max(1, page),
        taille=TAILLE_PAGE,
    )
    if resultat.total == 0:
        message = f"Aucun élément parmi les {description.pluriel}"
        if texte:
            message += f" ne correspond à « {texte} »"
        if any(filtres.values()):
            message += " avec ces filtres"
        message += "."
        if texte:
            proches = _suggestions(texte, _candidats(session, code))
            message += (
                f" Éléments proches : {', '.join(proches)}."
                if proches
                else " Aucun élément proche."
            )
        return message
    lignes = [
        f"{resultat.total} {description.pluriel if resultat.total > 1 else description.nom} "
        f"trouvé{'e' if description.feminin else ''}{'s' if resultat.total > 1 else ''}"
        + (f", page {resultat.page} sur {resultat.nb_pages}" if resultat.nb_pages > 1 else "")
        + " :"
    ]
    for ligne in resultat.lignes:
        morceaux = []
        for colonne in description.colonnes:
            valeur = _valeur(ligne.get(colonne.cle))
            if valeur:
                morceaux.append(f"{colonne.libelle.lower()} {valeur}")
        prefixe = f"n° {ligne['id']} : " if code in ("besoin", "indisponibilite") else ""
        lignes.append(f"- {prefixe}{' ; '.join(morceaux)}")
    return "\n".join(lignes)


def _candidats(session: Session, code: str) -> dict[str, str]:
    """Noms et codes comparés pour les suggestions de `chercher`."""
    if code == "enseignant":
        return {
            cle: f"{e.nom_affiche} ({e.identifiant})"
            for e in session.scalars(select(m.Enseignant))
            for cle in (e.nom, e.identifiant)
        }
    description = operations.entite(code)
    premiere = description.colonnes[0].cle
    candidats: dict[str, str] = {}
    page = operations.lister(session, description, taille=500, filtres={})
    for ligne in page.lignes:
        valeur = _valeur(ligne.get(premiere))
        if valeur:
            candidats[valeur] = valeur
    return candidats


def _horaire(
    session: Session, entree: Mapping[str, Any], jour: int, *, requis: bool = True
) -> tuple[int, int] | None:
    moment = _texte(entree, "moment")
    debut, fin = _heure(entree, "debut"), _heure(entree, "fin")
    if moment is not None and (debut is not None or fin is not None):
        raise ErreurDonneesInvalides("Donner un moment ou un début et une fin, pas les deux.")
    if moment is not None:
        return occupation.plage_du_moment(session, jour, moment)
    if debut is None and fin is None:
        if requis:
            raise ErreurDonneesInvalides(
                "Donner un moment (matin, apres_midi, journee) ou un début et une fin."
            )
        return None
    if debut is None or fin is None:
        raise ErreurDonneesInvalides("Donner le début et la fin du créneau.")
    if debut >= fin:
        raise ErreurDonneesInvalides(
            f"Le début ({minutes_vers_texte(debut)}) doit précéder la fin ({minutes_vers_texte(fin)})."
        )
    return debut, fin


def _semaines(numeros) -> str:
    """« la semaine 21 », « les semaines 1 à 10 et 12 »."""
    article = "la semaine" if len(numeros) == 1 else "les semaines"
    return f"{article} {plages_de_semaines(numeros)}"


def _salle_vue(salle: occupation.SalleVue, *, campus: bool = True) -> str:
    lieu = f"{salle.campus}, " if campus else ""
    return f"{salle.code} ({lieu}{salle.capacite} places, {salle.type_salle})"


def _salles_libres(session: Session, entree: Mapping[str, Any], contexte: ContexteOutil) -> str:
    semestre = _semestre(session, contexte, _texte(entree, "semestre"))
    jour, date = _jour(entree), _date(entree, "date")
    if jour is None and date is None:
        raise ErreurDonneesInvalides("Donner un jour de la semaine type ou une date.")
    if date is not None and jour is not None and date.weekday() != jour:
        raise ErreurDonneesInvalides(
            f"Le {date:%d/%m/%Y} est un {JOURS[date.weekday()]}, pas un {JOURS[jour]}."
        )
    horaire = _horaire(session, entree, date.weekday() if date is not None else jour)
    capacite = _entier(entree, "capacite_min")
    r = occupation.salles_libres(
        session,
        semestre_id=semestre.id,
        jour=None if date is not None else jour,
        date=date,
        debut=horaire[0],
        fin=horaire[1],
        capacite_min=capacite,
        campus=_texte(entree, "campus"),
        type_salle=_texte(entree, "type_salle"),
    )
    filtres = []
    if capacite is not None:
        filtres.append(f"au moins {capacite} places")
    for cle in ("campus", "type_salle"):
        if _texte(entree, cle):
            filtres.append(f"{'campus' if cle == 'campus' else 'type'} {_texte(entree, cle)}")
    if r.date is None:
        quand = (
            f"Semaine type du semestre {r.semestre_code} (version active n° {r.version_numero}, "
            f"{_nombre(r.nb_semaines, 'semaine')} de cours ce jour-là), {JOURS[r.jour]} "
            f"{_plage(r.debut, r.fin)}"
        )
    else:
        quand = (
            f"{presentation.date_longue(r.date).capitalize()}, {_plage(r.debut, r.fin)} "
            f"(version active n° {r.version_numero} du semestre {r.semestre_code})"
        )
    lignes = [quand + (", " + ", ".join(filtres) if filtres else "") + "."]
    if r.fermeture:
        lignes.append(f"Ce jour-là, l'établissement est fermé : {r.fermeture}.")
    entete = "Libres toutes les semaines" if r.date is None else "Libres"
    lignes.append(f"{entete} ({len(r.libres)}) :" if r.libres else f"{entete} : aucune.")
    par_campus: dict[str, list[str]] = {}
    for salle in r.libres:
        par_campus.setdefault(salle.campus, []).append(_salle_vue(salle, campus=False))
    for campus, salles in par_campus.items():
        lignes.append(f"- {campus} : {', '.join(salles)}")
    if r.libres_certaines_semaines:
        lignes.append("Libres certaines semaines seulement :")
        for salle, semaines in r.libres_certaines_semaines:
            lignes.append(
                f"- {_salle_vue(salle)} : libre {_semaines(semaines)} "
                f"({len(semaines)} sur {r.nb_semaines})"
            )
    if r.occupees:
        lignes.append("Occupées :")
        for o in r.occupees:
            semaines = (
                ""
                if not o.semaines or r.date is not None
                else f" ({_semaines(o.semaines)})"
                if len(o.semaines) < r.nb_semaines
                else ""
            )
            lignes.append(
                f"- {_salle_vue(o.salle)} : {_plage(o.debut, o.fin)} {o.libelle}{semaines}"
            )
    if r.exceptions:
        lignes.append("Exceptions datées (indisponibilités des salles libres) :")
        for e in r.exceptions:
            dates = ", ".join(f"{d:%d/%m/%Y}" for d in e.dates)
            motif = f" ({e.motif})" if e.motif else ""
            lignes.append(
                f"- {e.salle.code} ({e.salle.campus}) : indisponible le {dates}, {_plage(e.debut, e.fin)}{motif}"
            )
    return "\n".join(lignes)


def _emploi_du_temps(session: Session, entree: Mapping[str, Any], contexte: ContexteOutil) -> str:
    nature = _texte_requis(entree, "nature")
    code = _texte_requis(entree, "code")
    semestre = _semestre(session, contexte, _texte(entree, "semestre"))
    date, jour = _date(entree, "date"), _jour(entree)
    active = versions.version_active(session, semestre.id)
    if active is None:
        raise ErreurDonneesInvalides(
            f"Le semestre {semestre.code} n'a pas encore d'emploi du temps actif : lancer un "
            "calcul dans « Générer »."
        )
    if nature == "groupe":
        cibles = [(vues.Cible(vues.NatureCible.GROUPE, _groupe(session, contexte, code).id), None)]
    elif nature == "enseignant":
        cibles = [(vues.Cible(vues.NatureCible.ENSEIGNANT, _enseignant(session, code).id), None)]
    elif nature == "salle":
        cibles = [
            (vues.Cible(vues.NatureCible.SALLE, salle.id), salle.campus.code)
            for salle in _salles(session, code)
        ]
    else:
        raise ErreurDonneesInvalides(f"Nature inconnue : « {nature} » (groupe, enseignant, salle).")
    if date is not None and not semestre.date_debut <= date <= semestre.date_fin:
        raise ErreurDonneesInvalides(
            f"Le {date:%d/%m/%Y} est hors du semestre {semestre.code} (du "
            f"{semestre.date_debut:%d/%m/%Y} au {semestre.date_fin:%d/%m/%Y})."
        )
    # Sans séances datées, une date se lit dans la semaine type de sa semaine.
    datees = date is not None and active.nb_occurrences > 0
    numero_semaine = None
    if date is not None and not datees:
        semaine = semaine_de(date, semaines_entre(semestre.date_debut, semestre.date_fin))
        numero_semaine = semaine.numero if semaine is not None else None
    couche = vues.Couche.DATE if datees else vues.Couche.HEBDO
    affichage = (
        vues.Affichage.JOUR if (jour is not None and date is None) else vues.Affichage.SEMAINE
    )
    lignes: list[str] = []
    for cible, campus in cibles:
        vue = vues.construire_vue(
            session,
            version_id=active.id,
            cible=cible,
            couche=couche,
            affichage=affichage,
            date=date if datees else None,
            jour=jour if date is None else None,
            verifier=False,
            aujourdhui=contexte.aujourdhui,
        )
        blocs = sorted(vue.blocs, key=lambda b: (b.date or dt.date.min, b.jour, b.debut))
        if date is not None and not datees:
            blocs = [b for b in blocs if numero_semaine in b.semaines]
        if date is not None and jour is not None:
            blocs = [b for b in blocs if b.jour == jour]
        titre = vue.titre_cible + (f" ({campus})" if campus and len(cibles) > 1 else "")
        if date is None:
            periode = f"semaine type du semestre {semestre.code}"
        elif datees:
            lundi = date - dt.timedelta(days=date.weekday())
            periode = f"semaine du lundi {lundi:%d/%m/%Y} (séances datées)"
        else:
            lundi = date - dt.timedelta(days=date.weekday())
            periode = (
                f"semaine du lundi {lundi:%d/%m/%Y} (cette version n'a pas de séances datées : "
                f"semaine type de la semaine n° {numero_semaine})"
            )
        if jour is not None:
            periode += f", {JOURS[jour]}"
        lignes.append(
            f"Emploi du temps de {titre}, {periode}, version active n° {active.numero} : "
            f"{_nombre(len(blocs), 'séance')}."
        )
        for bloc in blocs[:SEANCES_MAX]:
            quand = JOURS[bloc.jour] + (f" {bloc.date:%d/%m/%Y}" if bloc.date else "")
            morceaux = [
                f"{bloc.type_code} {bloc.matiere}"
                + (f" ({bloc.precision})" if bloc.precision else "")
            ]
            if bloc.groupes:
                morceaux.append(", ".join(bloc.groupes))
            if bloc.salle:
                morceaux.append(
                    f"salle {bloc.salle}" + (f" ({bloc.campus})" if bloc.campus else "")
                )
            else:
                morceaux.append("sans salle")
            if bloc.enseignants:
                morceaux.append(", ".join(bloc.enseignants))
            if bloc.semaines_texte:
                morceaux.append(bloc.semaines_texte.lower())
            if bloc.partielle:
                morceaux.append("une partie du groupe")
            lignes.append(f"- {quand} {_plage(bloc.debut, bloc.fin)} : {' ; '.join(morceaux)}")
        if len(blocs) > SEANCES_MAX:
            lignes.append(f"… et {len(blocs) - SEANCES_MAX} autres séances.")
        if datees:
            for colonne in vue.jours:
                if colonne.fermeture:
                    lignes.append(f"{colonne.libelle} : fermé ({colonne.fermeture}).")
            if vue.a_programmer:
                lignes.append(
                    f"{_nombre(len(vue.a_programmer), 'séance')} à programmer cette semaine."
                )
        parametres = {
            "semestre_id": semestre.id,
            "version_id": active.id,
            f"{cible.nature.value}_id": cible.id,
        }
        if datees:
            parametres.update(couche="date", date=date.isoformat())
        if jour is not None and date is None:
            parametres.update(affichage="jour", jour=jour)
        contexte.lier(
            f"Voir l'emploi du temps de {titre}", "/emploi-du-temps?" + urlencode(parametres)
        )
    return "\n".join(lignes)


_GRAVITES = {
    "erreur": Gravite.ERREUR,
    "avertissement": Gravite.AVERTISSEMENT,
    "information": Gravite.INFORMATION,
}


def _points_a_traiter(session: Session, entree: Mapping[str, Any], contexte: ContexteOutil) -> str:
    semestre = _semestre(session, contexte, _texte(entree, "semestre"))
    nom_gravite = _texte(entree, "gravite")
    gravite = None
    if nom_gravite is not None:
        if nom_gravite not in _GRAVITES:
            raise ErreurDonneesInvalides(f"Gravité inconnue : « {nom_gravite} ».")
        gravite = _GRAVITES[nom_gravite]
    resultats = anomalies.a_traiter(session, semestre.id)
    contexte.lier("Voir les points à traiter", f"/a-traiter?semestre_id={semestre.id}")
    if not resultats:
        return f"Le semestre {semestre.code} n'a pas d'emploi du temps actif : rien à traiter."
    a_traiter = resultats[0]
    total = (
        "aucun point à traiter"
        if a_traiter.total == 0
        else f"{_nombre(a_traiter.total, 'point')} à traiter"
    )
    lignes = [
        f"Semestre {semestre.code}, version active n° {a_traiter.version.numero} : {total} "
        "(erreurs, avertissements et séances à programmer)."
    ]
    if not a_traiter.complet:
        motifs = [c.motif for c in (a_traiter.hebdo, a_traiter.datees) if c is not None and c.motif]
        lignes.append("Vérification incomplète : " + " ".join(motifs))
    for titre, groupes in (
        ("Semaine type", a_traiter.gravites_hebdo),
        ("Séances datées", a_traiter.gravites_datees),
    ):
        for groupe in groupes:
            if gravite is not None and groupe.gravite is not gravite:
                continue
            lignes.append(f"{titre}, {groupe.titre.lower()} : {groupe.total}")
            for contrainte in groupe.contraintes:
                lignes.append(f"- {contrainte.libelle} : {contrainte.total}")
                for ligne in contrainte.lignes[:3]:
                    lignes.append(f"  · {ligne.message}")
    if a_traiter.a_programmer and gravite in (None, Gravite.AVERTISSEMENT):
        lignes.append(f"Séances à programmer : {len(a_traiter.a_programmer)}")
        for seance in a_traiter.a_programmer[:3]:
            lignes.append(
                f"- {seance.date:%d/%m/%Y} {_plage(seance.debut, seance.fin)} {seance.libelle} : {seance.raison}"
            )
    return "\n".join(lignes)


def _lister_regles(session: Session, entree: Mapping[str, Any], contexte: ContexteOutil) -> str:
    annee_id, libelle = contexte.annee(session)
    groupe, enseignant, matiere = (_texte(entree, c) for c in ("groupe", "enseignant", "matiere"))
    details = regles.lister(
        session,
        annee_id,
        etat=_texte(entree, "etat"),
        type_regle=_texte(entree, "type"),
        groupe_id=_groupe(session, contexte, groupe).id if groupe else None,
        enseignant_id=_enseignant(session, enseignant).id if enseignant else None,
        matiere_id=_matiere(session, contexte, matiere).id if matiere else None,
    )
    texte = _texte(entree, "texte")
    if texte:
        cherche = _sans_accent(texte)
        details = [
            d
            for d in details
            if cherche
            in _sans_accent(
                " ".join(
                    (d.code, d.phrase, d.portee.cible_code or "", d.portee.cible_libelle or "")
                )
            )
        ]
    if not details:
        return f"Aucune règle personnalisée ne correspond en {libelle}."
    lignes = [f"{_nombre(len(details), 'règle')} en {libelle} :"]
    for d in details[:REGLES_MAX]:
        lignes.append(
            f"- {d.code} ({d.etat.libelle.lower()}, {d.niveau.libelle.lower()}, "
            f"{d.regle.periode_texte()}, {d.type.value}) : {d.phrase}"
        )
    if len(details) > REGLES_MAX:
        lignes.append(f"… et {len(details) - REGLES_MAX} autres : précisez la recherche.")
    return "\n".join(lignes)


def _expliquer_regle(session: Session, entree: Mapping[str, Any], contexte: ContexteOutil) -> str:
    annee_id, _ = contexte.annee(session)
    explication = regles.expliquer(session, annee_id, _texte_requis(entree, "code"))
    code = explication.detail.code
    contexte.lier(f"Voir la règle {code}", adresse_regle(code, annee_id))
    return explication.texte


# --------------------------------------------------------------------------- #
# Outils de proposition
# --------------------------------------------------------------------------- #


def _sans_nuls(valeurs: object) -> dict[str, Any]:
    if valeurs is None:
        return {}
    if not isinstance(valeurs, Mapping):
        raise ErreurDonneesInvalides("Un objet est attendu.")
    return {cle: valeur for cle, valeur in valeurs.items() if valeur is not None}


def _details_regle(regle) -> tuple[str, ...]:
    details = [
        f"Type : {regle.type.libelle.lower()}",
        f"Niveau : {regle.niveau.libelle.lower()}",
        f"Période : {regle.periode_texte()}",
    ]
    if regle.commentaire:
        details.append(f"Commentaire : {regle.commentaire}")
    return tuple(details)


def _brouillon_canonique(regle, annee_libelle: str) -> dict[str, Any]:
    periode: dict[str, Any] = {}
    if regle.semestre_code:
        periode["semestre"] = regle.semestre_code
    if regle.date_debut:
        periode["date_debut"] = regle.date_debut.isoformat()
    if regle.date_fin:
        periode["date_fin"] = regle.date_fin.isoformat()
    return {
        "type": regle.type.value,
        "portee": {"nature": regle.portee.nature.value, "cible": regle.portee.cible_code},
        "parametres": dict(regle.parametres),
        "niveau": regle.niveau.value,
        "annee": annee_libelle,
        "periode": periode,
        "commentaire": regle.commentaire,
    }


def _lien_regles(annee_id: int) -> Lien:
    return Lien("Ouvrir l'écran des règles", f"/regles?annee={annee_id}")


def _resultat_proposition(proposition: Proposition) -> str:
    return (
        f"Proposition {proposition.code} présentée à l'utilisateur : « {proposition.phrase} »"
        + (f" ({'; '.join(proposition.details)})" if proposition.details else "")
        + ". Rien n'est enregistré avant qu'il clique sur « Confirmer »."
    )


def _refus_validation(erreurs: Mapping[str, str]) -> ErreurEDT:
    return regles.ErreurSaisie(erreurs, "Proposition invalide : rien n'est présenté.")


def _proposer_regle(session: Session, entree: Mapping[str, Any], contexte: ContexteOutil) -> str:
    annee_id, libelle = contexte.annee(session)
    brouillon: dict[str, Any] = {
        "type": entree.get("type"),
        "portee": _sans_nuls(entree.get("portee")),
        "parametres": _sans_nuls(entree.get("parametres")),
        "periode": _sans_nuls(entree.get("periode")),
        "annee": libelle,
        "origine": "assistant",
    }
    if entree.get("niveau"):
        brouillon["niveau"] = entree["niveau"]
    if entree.get("commentaire"):
        brouillon["commentaire"] = entree["commentaire"]
    validation = regles.valider(session, brouillon)
    if not validation.valide:
        raise _refus_validation(validation.erreurs)
    regle = validation.regle
    proposition = contexte.ajouter(
        lambda numero: Proposition(
            numero=numero,
            nature=NatureProposition.REGLE,
            titre="Nouvelle règle",
            phrase=validation.phrase,
            details=_details_regle(regle),
            donnees={"annee_id": annee_id, "brouillon": _brouillon_canonique(regle, libelle)},
            lien_ecran=_lien_regles(annee_id),
        )
    )
    return _resultat_proposition(proposition)


def _proposer_modification_regle(
    session: Session, entree: Mapping[str, Any], contexte: ContexteOutil
) -> str:
    annee_id, _ = contexte.annee(session)
    code = _texte_requis(entree, "code").upper()
    changements: dict[str, Any] = {}
    if entree.get("portee") is not None:
        changements["portee"] = _sans_nuls(entree["portee"])
    if entree.get("parametres") is not None:
        changements["parametres"] = _sans_nuls(entree["parametres"])
    if entree.get("periode") is not None:
        changements["periode"] = _sans_nuls(entree["periode"])
    for cle in ("niveau", "commentaire"):
        if entree.get(cle) is not None:
            changements[cle] = entree[cle]
    if not any(changements.values()) and "periode" not in changements:
        raise ErreurDonneesInvalides(f"Aucun changement demandé pour la règle {code}.")
    avant = regles.lire(session, annee_id, code)
    validation = regles.valider_modification(session, annee_id, code, changements)
    if not validation.valide:
        raise _refus_validation(validation.erreurs)
    proposition = contexte.ajouter(
        lambda numero: Proposition(
            numero=numero,
            nature=NatureProposition.MODIFICATION,
            titre=f"Modifier {avant.code}",
            phrase=validation.phrase,
            phrase_avant=avant.phrase,
            details=_details_regle(validation.regle),
            donnees={"annee_id": annee_id, "code": avant.code, "changements": changements},
            lien_ecran=_lien_regles(annee_id),
        )
    )
    return _resultat_proposition(proposition) + f" Avant : « {avant.phrase} »."


def _proposer_etat_regle(
    session: Session, entree: Mapping[str, Any], contexte: ContexteOutil
) -> str:
    annee_id, _ = contexte.annee(session)
    code = _texte_requis(entree, "code").upper()
    etat = _texte_requis(entree, "etat")
    if etat not in (EtatRegle.ACTIVE.value, EtatRegle.INACTIVE.value):
        raise ErreurDonneesInvalides("« etat » vaut active ou inactive.")
    detail = regles.lire(session, annee_id, code)
    if detail.etat.value == etat:
        raise ErreurDonneesInvalides(
            f"La règle {detail.code} est déjà {detail.etat.libelle.lower()} : rien à proposer."
        )
    if etat == EtatRegle.ACTIVE.value:
        validation = regles.valider_modification(session, annee_id, code, {"etat": etat})
        if not validation.valide:
            raise _refus_validation(validation.erreurs)
        titre, suite = f"Activer {detail.code}", "Elle comptera au prochain calcul."
    else:
        titre, suite = f"Désactiver {detail.code}", "Elle n'aura plus d'effet au prochain calcul."
    proposition = contexte.ajouter(
        lambda numero: Proposition(
            numero=numero,
            nature=NatureProposition.ETAT,
            titre=titre,
            phrase=detail.phrase,
            details=(f"État actuel : {detail.etat.libelle.lower()}", suite),
            donnees={"annee_id": annee_id, "code": detail.code, "etat": etat},
            lien_ecran=_lien_regles(annee_id),
        )
    )
    return _resultat_proposition(proposition)


def _proposer_indisponibilite(
    session: Session, entree: Mapping[str, Any], contexte: ContexteOutil
) -> str:
    nature = _texte_requis(entree, "nature")
    code = _texte_requis(entree, "code")
    forme = _texte_requis(entree, "forme")
    valeurs: dict[str, Any] = {"cible": nature, "forme": forme}
    if nature == "enseignant":
        ressource = _enseignant(session, code)
        valeurs["enseignant_id"], nom = (
            ressource.id,
            f"{ressource.nom_affiche} ({ressource.identifiant})",
        )
    elif nature == "salle":
        salles = _salles(session, code)
        if len(salles) > 1:
            raise ErreurDonneesInvalides(
                f"Plusieurs salles portent le code « {code} » : "
                + ", ".join(f"{s.code} ({s.campus.code})" for s in salles)
                + ". Demandez laquelle et donnez-la sous la forme « CODE (CAMPUS) »."
            )
        ressource = salles[0]
        valeurs["salle_id"], nom = ressource.id, f"salle {ressource.code} ({ressource.campus.code})"
    elif nature == "groupe":
        ressource = _groupe(session, contexte, code)
        valeurs["groupe_id"], nom = ressource.id, f"groupe {ressource.code}"
    else:
        raise ErreurDonneesInvalides(f"Nature inconnue : « {nature} » (enseignant, salle, groupe).")
    jour = _jour(entree)
    date_debut, date_fin = _date(entree, "date_debut"), _date(entree, "date_fin")
    if forme == "recurrente":
        if jour is None:
            raise ErreurDonneesInvalides("Une indisponibilité récurrente a un jour.")
        if date_debut or date_fin:
            raise ErreurDonneesInvalides("Une indisponibilité récurrente n'a pas de dates.")
        valeurs["jour"] = jour
        jour_grille = jour
    elif forme == "datee":
        if date_debut is None:
            raise ErreurDonneesInvalides("Une indisponibilité datée a au moins une date de début.")
        date_fin = date_fin or date_debut
        valeurs.update(date_debut=date_debut.isoformat(), date_fin=date_fin.isoformat())
        jour_grille = date_debut.weekday()
    else:
        raise ErreurDonneesInvalides(f"Forme inconnue : « {forme} » (recurrente, datee).")
    moment = _texte(entree, "moment")
    horaire = None if moment == "journee" else _horaire(session, entree, jour_grille, requis=False)
    if horaire is not None:
        valeurs.update(debut_min=horaire[0], fin_min=horaire[1])
    if _texte(entree, "motif"):
        valeurs["motif"] = _texte(entree, "motif")
    operations.valider_creation(session, "indisponibilite", valeurs)

    plage = (
        "toute la journée"
        if horaire is None
        else f"de {minutes_vers_texte(horaire[0])} à {minutes_vers_texte(horaire[1])}"
    )
    if forme == "recurrente":
        quand = f"chaque {JOURS[jour]} {plage}"
    elif date_fin == date_debut:
        quand = f"le {JOURS[date_debut.weekday()]} {date_debut:%d/%m/%Y}, {plage}"
    else:
        quand = f"du {date_debut:%d/%m/%Y} au {date_fin:%d/%m/%Y}, {plage}"
    phrase = f"{nom[:1].upper()}{nom[1:]}, {quand}"
    semestre = semestre_par_defaut(session, date_debut or contexte.aujourdhui)
    seances = occupation.seances_sur_creneau(
        session,
        semestre_id=semestre.id,
        cible=nature,
        ressource_id=ressource.id,
        debut=horaire[0] if horaire else 0,
        fin=horaire[1] if horaire else 24 * 60,
        jour=jour if forme == "recurrente" else None,
        date_debut=date_debut,
        date_fin=date_fin,
    )
    details = []
    if valeurs.get("motif"):
        details.append(f"Motif : {valeurs['motif']}")
    if seances:
        details.append(
            f"{_nombre(len(seances), 'séance')} de la version active du semestre {semestre.code} "
            f"{'tombe' if len(seances) == 1 else 'tombent'} sur ce créneau : "
            + " ; ".join(seances[:3])
            + (" …" if len(seances) > 3 else "")
        )
    else:
        details.append(
            f"Aucune séance de la version active du semestre {semestre.code} ne tombe sur ce créneau."
        )
    proposition = contexte.ajouter(
        lambda numero: Proposition(
            numero=numero,
            nature=NatureProposition.INDISPONIBILITE,
            titre="Indisponibilité",
            phrase=phrase,
            details=tuple(details),
            donnees={"valeurs": valeurs},
            lien_ecran=Lien("Ouvrir les indisponibilités", "/indisponibilites"),
        )
    )
    return _resultat_proposition(proposition)


def _preparer_formulaire_besoin(
    session: Session, entree: Mapping[str, Any], contexte: ContexteOutil
) -> str:
    semestre = _semestre(session, contexte, _texte(entree, "semestre"))
    matiere = _matiere(session, contexte, _texte_requis(entree, "matiere"))
    type_seance = _type_seance(session, _texte_requis(entree, "type_seance"))
    codes_groupes = entree.get("groupes")
    if not isinstance(codes_groupes, list) or not codes_groupes:
        raise ErreurDonneesInvalides("Au moins un groupe est attendu (codes).")
    groupes = [_groupe(session, contexte, str(c)) for c in codes_groupes]
    enseignants = [_enseignant(session, str(c)) for c in (entree.get("enseignants") or [])]
    duree = _entier(entree, "duree_minutes")
    nombre = _entier(entree, "seances_par_semaine")
    if duree is None or nombre is None:
        raise ErreurDonneesInvalides(
            "La durée d'une séance et le nombre par semaine sont attendus."
        )
    valeurs = {
        "semestre_id": semestre.id,
        "matiere_id": matiere.id,
        "type_seance_id": type_seance.id,
        "groupes": [g.id for g in groupes],
        "enseignants": [e.id for e in enseignants],
        "duree_min": duree,
        "mode_volume": "par_semaine",
        "seances_par_semaine": nombre,
    }
    operations.valider_creation(session, "besoin", valeurs)
    description = operations.entite("besoin")
    parametres: list[tuple[str, str]] = []
    for nom, valeur in valeurs.items():
        champ = description.champ(nom)
        if isinstance(valeur, list):
            parametres.extend((nom, str(v)) for v in valeur)
        else:
            parametres.append((nom, texte_valeur(champ, valeur)))
    adresse = "/besoins/nouveau?" + urlencode(parametres)
    contexte.lier("Ouvrir le formulaire prérempli du besoin", adresse)
    resume = (
        f"Nouveau besoin à vérifier : {type_seance.code} {matiere.libelle} ({matiere.code}), "
        f"{', '.join(g.code for g in groupes)}, {_nombre(nombre, 'séance')} de "
        f"{duree_vers_texte(duree)} par semaine, semestre {semestre.code}"
        + (
            f", enseignants {', '.join(e.nom_affiche for e in enseignants)}"
            if enseignants
            else ", enseignant à désigner"
        )
        + "."
    )
    voisins = [
        b
        for b in session.scalars(
            select(m.Besoin)
            .where(m.Besoin.semestre_id == semestre.id, m.Besoin.matiere_id == matiere.id)
            .options(selectinload(m.Besoin.groupes), selectinload(m.Besoin.type_seance))
        )
        if {g.id for g in b.groupes} & {g.id for g in groupes}
    ]
    lignes = [
        resume,
        "Le formulaire prérempli est proposé à l'utilisateur par un lien ; il vérifie et "
        "enregistre lui-même. Rien n'est enregistré.",
    ]
    if voisins:
        lignes.append("Besoins déjà présents pour cette matière et ces groupes :")
        for besoin in voisins[:5]:
            lignes.append(
                f"- n° {besoin.id} : {besoin.type_seance.code}, "
                f"{', '.join(sorted(g.code for g in besoin.groupes))}, {duree_vers_texte(besoin.duree_min)}"
            )
            contexte.lier(f"Ouvrir le besoin n° {besoin.id}", f"/besoins/{besoin.id}")
    else:
        lignes.append("Aucun besoin de cette matière pour ces groupes dans ce semestre.")
    return "\n".join(lignes)


_EXECUTANTS: dict[str, Callable[[Session, Mapping[str, Any], ContexteOutil], str]] = {
    "chercher": _chercher,
    "emploi_du_temps": _emploi_du_temps,
    "expliquer_regle": _expliquer_regle,
    "lire_contexte": _lire_contexte,
    "lister_regles": _lister_regles,
    "points_a_traiter": _points_a_traiter,
    "preparer_formulaire_besoin": _preparer_formulaire_besoin,
    "proposer_etat_regle": _proposer_etat_regle,
    "proposer_indisponibilite": _proposer_indisponibilite,
    "proposer_modification_regle": _proposer_modification_regle,
    "proposer_regle": _proposer_regle,
    "salles_libres": _salles_libres,
}
