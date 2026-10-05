"""Services des référentiels : édition de toutes les données qui décrivent l'établissement.

Chaque entité (campus, salle, enseignant, groupe, besoin...) est décrite une
fois, de façon déclarative (`description.Entite`), dans le module de sa
famille :

- `lieux` : campus (et matrice des trajets), types de salle, équipements, salles ;
- `personnes` : enseignants, groupes (arbre, chevauchements), étudiants ;
- `enseignements` : matières, types de séance, besoins, indisponibilités ;
- `calendrier` : années scolaires, semestres, jours fermés, créneaux de la grille ;
- `reglages` : paramètres globaux saisis dans un formulaire.

Les opérations génériques (`operations`) s'appliquent à toutes les entités
par leur code : `lister`, `lire`, `creer`, `modifier`, `analyser_suppression`,
`supprimer`, `options`, `decrire`. Elles ne valident jamais la transaction et
renvoient des valeurs sérialisables (réutilisables par une API ou un
assistant). Les opérations propres à une entité sont dans son module
(`dupliquer_besoin`, `charges_hebdomadaires`, `arbre_groupes`,
`matrice_trajets`, `enregistrer_trajets`, `creer_annee_par_defaut`,
`preremplir_jours_feries`).
"""

from edt.services.referentiels.operations import (
    AnalyseSuppression,
    ErreurSaisie,
    PageListe,
    analyser_suppression,
    creer,
    decrire,
    entite,
    lire,
    lister,
    modifier,
    options,
    supprimer,
)

__all__ = [
    "AnalyseSuppression",
    "ErreurSaisie",
    "PageListe",
    "analyser_suppression",
    "creer",
    "decrire",
    "entite",
    "lire",
    "lister",
    "modifier",
    "options",
    "supprimer",
]
