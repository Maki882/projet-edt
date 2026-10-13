"""Niveaux des contraintes appliqués aux anomalies (nécessaire, important, facultatif).

Quand une vérification reçoit des niveaux (`Niveaux`, voir
`edt.domaine.contraintes`) :

- seules les contraintes présentes sont vérifiées : **une clé absente est
  une contrainte ignorée** ;
- la gravité d'une anomalie suit le niveau de sa contrainte : nécessaire
  donne une erreur, important un avertissement, facultatif une
  information ; elle ne dépasse **jamais** la gravité que la règle donne
  déjà (plafond) : l'information d'une séance imposée reste une
  information, l'avertissement « salle à définir » n'est pas remonté en erreur ;
- `Anomalie.niveau` porte le niveau appliqué, et le message d'une entorse à
  une règle importante ou facultative commence par `PREFIXES_ENTORSE[niveau]`.

Sans niveaux, rien ne change : tout est vérifié en dur et `niveau` vaut `None`.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterable, Mapping

from edt.domaine.contraintes import CodeContrainte, Niveau, Niveaux, contrainte, niveau
from edt.domaine.enumerations import Gravite
from edt.erreurs import ErreurDonneesInvalides
from edt.verification.contexte import ORDRE_GRAVITE, resoudre_codes
from edt.verification.contrat import PREFIXES_ENTORSE, Anomalie

journal = logging.getLogger(__name__)

GRAVITE_DU_NIVEAU: dict[Niveau, Gravite] = {
    Niveau.NECESSAIRE: Gravite.ERREUR,
    Niveau.IMPORTANT: Gravite.AVERTISSEMENT,
    Niveau.FACULTATIF: Gravite.INFORMATION,
}
"""Gravité maximale d'une entorse à une contrainte de ce niveau."""

_ARTICLES = ("La ", "Le ", "Les ", "L'", "Un ", "Une ")


def valider_niveaux(niveaux: Mapping[str, str]) -> Niveaux:
    """Niveaux reçus, typés et validés (les clés absentes restent absentes : contraintes ignorées).

    Accepte des codes et des niveaux en texte ou typés. Lève
    `ErreurDonneesInvalides` pour un code ou un niveau inconnu, et pour un
    niveau autre que « nécessaire » sur une contrainte qui définit le problème
    (`Contrainte.modifiable` faux).
    """
    if not isinstance(niveaux, Mapping):
        raise ErreurDonneesInvalides(
            "Les niveaux des contraintes se donnent sous forme de dictionnaire « code : niveau » "
            f"(lu : {type(niveaux).__name__})."
        )
    valides: Niveaux = {}
    for code, valeur in niveaux.items():
        entree = contrainte(code)
        retenu = valeur if isinstance(valeur, Niveau) else niveau(valeur)
        if not entree.modifiable and retenu is not Niveau.NECESSAIRE:
            raise ErreurDonneesInvalides(
                f"La contrainte « {entree.libelle} » ({entree.code}) définit le problème : elle "
                f"reste nécessaire, son niveau ne peut pas être « {retenu.libelle} »."
            )
        valides[entree.code] = retenu
    return valides


def choisir_contraintes(
    contraintes_actives: Iterable[str] | None, niveaux: Mapping[str, str] | None
) -> tuple[Niveaux | None, frozenset[CodeContrainte]]:
    """Niveaux à appliquer (ou `None`) et codes à vérifier.

    Avec `niveaux`, les codes vérifiés sont ses clés et `contraintes_actives`
    est ignoré ; sans, c'est le comportement d'origine (`resoudre_codes`).
    """
    if niveaux is None:
        return None, resoudre_codes(contraintes_actives)
    valides = valider_niveaux(niveaux)
    if contraintes_actives is not None:
        journal.debug("Niveaux fournis : la liste de contraintes actives est ignorée.")
    return valides, frozenset(valides)


def appliquer_niveaux(anomalies: Iterable[Anomalie], niveaux: Niveaux) -> list[Anomalie]:
    """Anomalies dont la gravité, le niveau et le début du message suivent `niveaux`.

    Chaque anomalie a pour code une clé de `niveaux` (seules ces contraintes
    sont vérifiées) ; une anomalie d'un autre code est une erreur de programmation.
    """
    resultat = []
    for anomalie in anomalies:
        if anomalie.code not in niveaux:
            raise ValueError(
                f"Anomalie {anomalie.code} produite alors que la contrainte n'est pas vérifiée."
            )
        retenu = niveaux[anomalie.code]
        plafond = GRAVITE_DU_NIVEAU[retenu]
        gravite = max(anomalie.gravite, plafond, key=ORDRE_GRAVITE.__getitem__)
        resultat.append(
            dataclasses.replace(
                anomalie,
                gravite=gravite,
                niveau=retenu,
                message=prefixer(anomalie.message, retenu),
            )
        )
    return resultat


def prefixer(message: str, retenu: Niveau) -> str:
    """Message précédé de « Entorse à une règle importante : » (ou facultative) ; inchangé si nécessaire.

    Après les deux-points, un article de début de phrase passe en minuscule
    (« … : la salle N204 accueille… ») ; un nom propre garde sa majuscule.
    """
    prefixe = PREFIXES_ENTORSE.get(retenu)
    if prefixe is None:
        return message
    if message.startswith(_ARTICLES):
        message = message[:1].lower() + message[1:]
    return prefixe + message
