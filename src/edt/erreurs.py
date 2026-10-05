"""Hiérarchie unique des erreurs métier de l'application.

Les services lèvent ces erreurs ; la couche web les traduit en page lisible
(voir `edt.web.erreurs`) et la ligne de commande en code de sortie non nul
(voir `edt.cli`). Le message est toujours rédigé en français pour un
utilisateur non informaticien ; `details` porte les lignes complémentaires
(par exemple la liste des besoins fautifs).
"""

from __future__ import annotations

from collections.abc import Iterable


class ErreurEDT(Exception):
    """Racine de toutes les erreurs métier.

    `message` est affichable tel quel à l'utilisateur ; `details` est une
    liste de lignes complémentaires, éventuellement vide.
    """

    def __init__(self, message: str, details: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.details: tuple[str, ...] = tuple(details)

    def __str__(self) -> str:
        if not self.details:
            return self.message
        return self.message + "\n" + "\n".join(f"  - {ligne}" for ligne in self.details)


class ErreurConfiguration(ErreurEDT):
    """Variable d'environnement absente ou invalide, dossier inaccessible."""


class ErreurDonneesInvalides(ErreurEDT):
    """Une donnée saisie ou importée ne respecte pas le format ou les règles attendus."""


class ErreurIntrouvable(ErreurEDT):
    """L'élément demandé n'existe pas (ou plus)."""


class ErreurRegleMetier(ErreurEDT):
    """L'opération est refusée car elle contredit une règle métier.

    Exemples : doublon sur une clé d'unicité, suppression d'un élément encore
    utilisé, activation d'une version d'un autre semestre.
    """


class ErreurCalculEnCours(ErreurRegleMetier):
    """Un calcul est déjà en cours : un seul calcul à la fois."""


class ErreurInfaisable(ErreurEDT):
    """Il est prouvé qu'aucun emploi du temps ne respecte toutes les contraintes actives."""


class ErreurTempsDepasse(ErreurEDT):
    """Aucune solution trouvée dans le temps imparti, sans preuve d'impossibilité."""


class ErreurNonLivre(ErreurEDT):
    """La fonctionnalité appelée appartient à un lot qui n'est pas encore livré.

    Levée par les implémentations provisoires des contrats partagés : mieux
    vaut une erreur explicite qu'un résultat vide pris pour un succès.
    """
