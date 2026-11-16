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

Règles personnalisées (code `REGLE`) : elles sont vérifiées si `REGLE` l'est.
`niveaux_regles` (identifiant de règle → niveau) dit lesquelles et à quel
niveau ; sans lui, chaque règle active de l'instantané l'est au sien. Une
règle absente est **ignorée, comme si elle n'existait pas** : pour une règle de
réglage (jours, déjeuner, pause), le standard reprend alors sa place
(`choisir_regles` rend un instantané sans elle). Une anomalie de règle prend
toujours le niveau de **sa règle** (même quand les contraintes du catalogue
sont vérifiées en dur), avec le même plafond et le même début de message.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterable, Mapping

from edt.domaine.contraintes import CodeContrainte, Niveau, Niveaux, contrainte, niveau
from edt.domaine.enumerations import Gravite
from edt.domaine.instantane import Instantane
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

_DEBUTS_NOMS_COMMUNS = ("La séance ", "La salle ", "Le groupe ", "Les groupes ")
"""Débuts des messages du vérificateur mis en minuscule après le préfixe d'entorse.

Liste fermée : tout autre début garde sa majuscule, à commencer par un nom
propre qui commence comme un article (l'enseignant « Le Gall »).
"""


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
    est ignoré ; sans, les codes sont ceux de `contraintes_actives`, vérifiés en
    dur (`resoudre_codes`).
    Aucun code (liste vide, niveaux vides) ne vérifie rien : permis ici (le
    contrôle d'une solution peut n'avoir rien de nécessaire à vérifier), mais
    journalisé en avertissement ; les services de vérification le refusent.
    """
    if niveaux is None:
        valides, codes = None, resoudre_codes(contraintes_actives)
    else:
        valides = valider_niveaux(niveaux)
        codes = frozenset(valides)
        if contraintes_actives is not None:
            journal.debug("Niveaux fournis : la liste de contraintes actives est ignorée.")
    if not codes:
        journal.warning(
            "Aucune contrainte à vérifier : la vérification ne contrôle rien et ne peut relever "
            "aucune anomalie."
        )
    return valides, codes


def valider_niveaux_regles(niveaux_regles: Mapping[int, str]) -> dict[int, Niveau]:
    """Niveaux des règles reçus, typés (identifiant entier → `Niveau`) ; erreur sinon."""
    if not isinstance(niveaux_regles, Mapping):
        raise ErreurDonneesInvalides(
            "Les niveaux des règles personnalisées se donnent sous forme de dictionnaire "
            f"« identifiant : niveau » (lu : {type(niveaux_regles).__name__})."
        )
    valides: dict[int, Niveau] = {}
    for regle_id, valeur in niveaux_regles.items():
        if isinstance(regle_id, bool) or not isinstance(regle_id, int):
            raise ErreurDonneesInvalides(
                f"Identifiant de règle personnalisée invalide : {regle_id!r} (un entier est attendu)."
            )
        valides[regle_id] = valeur if isinstance(valeur, Niveau) else niveau(valeur)
    return valides


def choisir_regles(
    instantane: Instantane,
    codes: frozenset[CodeContrainte],
    niveaux_regles: Mapping[int, str] | None,
) -> tuple[Instantane, dict[int, Niveau]]:
    """Instantané à vérifier et niveau de chaque règle personnalisée retenue.

    Sans `REGLE` dans `codes`, aucune règle ; sinon celles de `niveaux_regles`
    (toutes, à leur niveau, s'il vaut `None`). Si des règles de l'instantané
    ne sont pas retenues, l'instantané renvoyé ne les contient plus : ses
    réglages (`Instantane.reglages`) sont ceux d'un établissement sans elles.
    Un identifiant de `niveaux_regles` absent de l'instantané (règle supprimée,
    désactivée ou hors du semestre) est sans effet, noté dans le journal.
    """
    if CodeContrainte.REGLE not in codes:
        retenus: dict[int, Niveau] = {}
    elif niveaux_regles is None:
        retenus = {regle.id: regle.niveau for regle in instantane.regles}
    else:
        demandes = valider_niveaux_regles(niveaux_regles)
        presentes = {regle.id for regle in instantane.regles}
        absentes = sorted(set(demandes) - presentes)
        if absentes:
            journal.debug(
                "Niveaux reçus pour %d règle(s) absente(s) de l'instantané : %s",
                len(absentes),
                ", ".join(map(str, absentes)),
            )
        retenus = {regle_id: n for regle_id, n in demandes.items() if regle_id in presentes}
    if len(retenus) == len(instantane.regles):
        return instantane, retenus
    ignorees = [regle.code for regle in instantane.regles if regle.id not in retenus]
    journal.debug(
        "Règles personnalisées ignorées pour cette vérification : %s", ", ".join(ignorees)
    )
    restreint = dataclasses.replace(
        instantane, regles=tuple(r for r in instantane.regles if r.id in retenus)
    )
    return restreint, retenus


def appliquer_niveaux(
    anomalies: Iterable[Anomalie],
    niveaux: Niveaux | None,
    niveaux_regles: Mapping[int, Niveau] | None = None,
) -> list[Anomalie]:
    """Anomalies dont la gravité, le niveau et le début du message suivent les niveaux.

    Une anomalie de règle (`REGLE`) suit le niveau de sa règle
    (`niveaux_regles[regle_id]`) ; une autre, celui de sa contrainte dans
    `niveaux` (inchangée si `niveaux` vaut `None` : vérification en dur).
    Une anomalie d'une contrainte ou d'une règle non vérifiée est une erreur
    de programmation.
    """
    resultat = []
    for anomalie in anomalies:
        if anomalie.code is CodeContrainte.REGLE:
            if niveaux_regles is None or anomalie.regle_id not in niveaux_regles:
                raise ValueError(
                    f"Anomalie de la règle {anomalie.regle_code} produite alors qu'elle n'est "
                    "pas vérifiée."
                )
            retenu = niveaux_regles[anomalie.regle_id]
        elif niveaux is None:
            resultat.append(anomalie)
            continue
        elif anomalie.code not in niveaux:
            raise ValueError(
                f"Anomalie {anomalie.code} produite alors que la contrainte n'est pas vérifiée."
            )
        else:
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

    Après les deux-points, un début de phrase du vérificateur passe en
    minuscule (« … : la salle N204 accueille… ») ; un nom propre garde sa
    majuscule (« … : Le Gall dépasse… », voir `_DEBUTS_NOMS_COMMUNS`).
    """
    prefixe = PREFIXES_ENTORSE.get(retenu)
    if prefixe is None:
        return message
    if message.startswith(_DEBUTS_NOMS_COMMUNS):
        message = message[:1].lower() + message[1:]
    return prefixe + message
