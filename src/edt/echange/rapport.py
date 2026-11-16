"""Rapport d'un import : signalements ligne par ligne, bilans par feuille, aperçu.

Tout ce qu'un import ne fait pas entrer tel quel laisse une trace ici :
ligne rejetée, séance mise de côté, doublon fusionné, colonne non reconnue,
repli d'encodage. Un signalement de gravité « erreur » correspond toujours
à une ligne (ou un fichier) qui n'est pas entrée.

Toutes les classes sont sérialisables en JSON (`vers_dict`) et le rapport
complet s'exporte en CSV (`rapport_csv`) pour être téléchargé.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from edt.domaine.enumerations import Gravite
from edt.echange.valeurs import neutraliser_formule

LIBELLES_COMPTEURS: dict[str, tuple[str, str]] = {
    "crees": ("créé", "créés"),
    "mis_a_jour": ("mis à jour", "mis à jour"),
    "inchanges": ("inchangé", "inchangés"),
    "rejetes": ("rejeté", "rejetés"),
    "fusionnes": ("doublon fusionné", "doublons fusionnés"),
    "lues": ("lue", "lues"),
    "retenues": ("retenue", "retenues"),
    "annulees": ("annulée, non comptée", "annulées, non comptées"),
    "ecartees": ("mise de côté", "mises de côté"),
    "retirees": ("retirée (jour fermé)", "retirées (jour fermé)"),
    "supprimes": ("supprimé", "supprimés"),
}
"""Libellé affiché de chaque compteur d'un bilan (singulier, pluriel), dans l'ordre d'affichage."""

COMPTEURS_DE_REJET = ("rejetes", "ecartees")
"""Compteurs qui désignent des lignes qui ne sont pas entrées."""

MAX_SIGNALEMENTS = 20_000
"""Signalements détaillés au plus par import ; au-delà, ils sont seulement comptés."""


@dataclass(frozen=True)
class Signalement:
    """Une ligne du rapport.

    - `gravite` : erreur (ligne ou fichier qui n'entre pas), avertissement
      (entré, mais à vérifier), information (trace d'un traitement normal) ;
    - `fichier`, `feuille`, `ligne`, `colonne` : position dans les fichiers
      envoyés (vides quand le signalement concerne tout l'import) ;
    - `message` : phrase française lisible par un non-informaticien.
    """

    gravite: Gravite
    message: str
    fichier: str = ""
    feuille: str = ""
    ligne: int | None = None
    colonne: str = ""

    @property
    def position(self) -> str:
        """Position lisible : « salles.csv, feuille salles, ligne 4, colonne capacite »."""
        morceaux = []
        if self.fichier:
            morceaux.append(self.fichier)
        if self.feuille and self.feuille != self.fichier:
            morceaux.append(f"feuille {self.feuille}")
        if self.ligne is not None:
            morceaux.append(f"ligne {self.ligne}")
        if self.colonne:
            morceaux.append(f"colonne {self.colonne}")
        return ", ".join(morceaux)

    def vers_dict(self) -> dict:
        """Représentation JSON."""
        return {
            "gravite": self.gravite.value,
            "fichier": self.fichier,
            "feuille": self.feuille,
            "ligne": self.ligne,
            "colonne": self.colonne,
            "message": self.message,
        }


class ListeSignalements(list):
    """Liste de signalements bornée : au-delà de `maximum`, les suivants sont comptés, pas gardés.

    Un fichier hostile ne doit pas faire grossir le rapport sans fin : les
    bilans comptent toujours toutes les lignes, seul le détail est borné.
    """

    def __init__(self, maximum: int = MAX_SIGNALEMENTS) -> None:
        super().__init__()
        self.maximum = maximum
        self.omis = 0
        self.erreurs_omises = 0

    def append(self, signalement: Signalement) -> None:
        if len(self) < self.maximum:
            super().append(signalement)
            return
        self.omis += 1
        if signalement.gravite is Gravite.ERREUR:
            self.erreurs_omises += 1

    def extend(self, signalements) -> None:  # noqa: D102 - même contrat que list.extend
        for signalement in signalements:
            self.append(signalement)

    def __iadd__(self, signalements):
        self.extend(signalements)
        return self


@dataclass
class Bilan:
    """Compteurs d'une feuille ou d'un objet importé (« 12 créés, 30 mis à jour... »)."""

    objet: str
    compteurs: dict[str, int] = field(default_factory=dict)

    def ajouter(self, compteur: str, nombre: int = 1) -> None:
        """Ajoute `nombre` au compteur `compteur` (une clé de `LIBELLES_COMPTEURS`) ; 0 ne crée rien."""
        if compteur not in LIBELLES_COMPTEURS:
            raise KeyError(f"Compteur de bilan inconnu : {compteur!r}")
        if nombre:
            self.compteurs[compteur] = self.compteurs.get(compteur, 0) + nombre

    def nombre(self, compteur: str) -> int:
        """Valeur d'un compteur (0 s'il n'a jamais été incrémenté)."""
        return self.compteurs.get(compteur, 0)

    @property
    def texte(self) -> str:
        """Résumé lisible : « 12 créés, 30 mis à jour, 1 rejeté »."""
        parties = [
            f"{self.compteurs[cle]} {singulier if self.compteurs[cle] == 1 else pluriel}"
            for cle, (singulier, pluriel) in LIBELLES_COMPTEURS.items()
            if self.compteurs.get(cle)
        ]
        return ", ".join(parties) if parties else "rien à traiter"

    def vers_dict(self) -> dict:
        """Représentation JSON."""
        return {"objet": self.objet, "compteurs": dict(self.compteurs), "texte": self.texte}


@dataclass(frozen=True)
class Apercu:
    """Les premières lignes d'un tableau lu, pour vérifier la lecture avant d'appliquer."""

    fichier: str
    feuille: str
    entetes: tuple[str, ...]
    lignes: tuple[tuple[str, ...], ...]
    nb_lignes: int

    def vers_dict(self) -> dict:
        """Représentation JSON."""
        return {
            "fichier": self.fichier,
            "feuille": self.feuille,
            "entetes": list(self.entetes),
            "lignes": [list(ligne) for ligne in self.lignes],
            "nb_lignes": self.nb_lignes,
        }


@dataclass(frozen=True)
class RapportImport:
    """Résultat complet d'un import (simulé ou appliqué).

    - `mode` : valeur de `ModeImport` ;
    - `simulation` : vrai si rien n'a été écrit ;
    - `bilans` : un par feuille (données) ou par objet (emploi du temps) ;
    - `signalements` : le rapport ligne par ligne ;
    - `apercus` : les 20 premières lignes de chaque tableau lu ;
    - `version_id` : version d'emploi du temps créée (import d'emploi du temps) ;
    - `semaines_types` : semaine type retenue, par promotion (phrases) ;
    - `besoins` : besoins reconstitués (import d'emploi du temps), une ligne
      lisible par besoin ;
    - `signalements_omis`, `erreurs_omises` : signalements comptés mais non
      détaillés, au-delà de `MAX_SIGNALEMENTS`.
    """

    mode: str
    simulation: bool
    fichiers: tuple[str, ...]
    bilans: tuple[Bilan, ...]
    signalements: tuple[Signalement, ...]
    apercus: tuple[Apercu, ...] = ()
    version_id: int | None = None
    semaines_types: tuple[str, ...] = ()
    besoins: tuple[dict, ...] = ()
    signalements_omis: int = 0
    erreurs_omises: int = 0

    @property
    def nb_signalements(self) -> int:
        """Nombre total de signalements, détaillés ou non."""
        return len(self.signalements) + self.signalements_omis

    @property
    def nb_erreurs(self) -> int:
        """Nombre de signalements de gravité « erreur », détaillés ou non."""
        return (
            sum(1 for s in self.signalements if s.gravite is Gravite.ERREUR) + self.erreurs_omises
        )

    @property
    def nb_rejets(self) -> int:
        """Nombre de lignes rejetées ou mises de côté, toutes feuilles confondues."""
        return sum(bilan.nombre(cle) for bilan in self.bilans for cle in COMPTEURS_DE_REJET)

    @property
    def a_des_ecritures(self) -> bool:
        """Vrai si l'application écrirait quelque chose (création, mise à jour, suppression...).

        Faux quand tout est rejeté, mis de côté ou déjà identique : appliquer ne
        changerait rien.
        """
        # « fusionnes » : doublon du fichier écarté à la lecture au profit de sa première ligne.
        sans_ecriture = (
            *COMPTEURS_DE_REJET,
            "inchanges",
            "fusionnes",
            "lues",
            "annulees",
            "retirees",
        )
        return any(
            nombre and cle not in sans_ecriture
            for bilan in self.bilans
            for cle, nombre in bilan.compteurs.items()
        )

    @property
    def a_des_rejets(self) -> bool:
        """Vrai si quelque chose n'est pas entré (ligne, séance ou fichier entier)."""
        return self.nb_erreurs > 0 or self.nb_rejets > 0

    def bilan(self, objet: str) -> Bilan:
        """Bilan d'une feuille ou d'un objet (un bilan vide s'il n'y en a pas)."""
        for bilan in self.bilans:
            if bilan.objet == objet:
                return bilan
        return Bilan(objet)

    def vers_dict(self) -> dict:
        """Représentation JSON."""
        return {
            "mode": self.mode,
            "simulation": self.simulation,
            "fichiers": list(self.fichiers),
            "bilans": [bilan.vers_dict() for bilan in self.bilans],
            "signalements": [s.vers_dict() for s in self.signalements],
            "apercus": [apercu.vers_dict() for apercu in self.apercus],
            "version_id": self.version_id,
            "semaines_types": list(self.semaines_types),
            "besoins": [dict(besoin) for besoin in self.besoins],
            "nb_erreurs": self.nb_erreurs,
            "nb_rejets": self.nb_rejets,
            "signalements_omis": self.signalements_omis,
        }


def rapport_csv(rapport: RapportImport) -> bytes:
    """Le rapport en CSV (UTF-8 avec BOM, séparateur point-virgule), lisible dans un tableur."""
    tampon = io.StringIO()
    ecrivain = csv.writer(tampon, delimiter=";", lineterminator="\r\n")
    ecrivain.writerow(["gravite", "fichier", "feuille", "ligne", "colonne", "message"])
    etat = "simulation (rien n'a été écrit)" if rapport.simulation else "import appliqué"
    for bilan in rapport.bilans:
        ecrivain.writerow(["bilan", "", bilan.objet, "", "", f"{bilan.texte} - {etat}"])
    for phrase in rapport.semaines_types:
        ecrivain.writerow(["information", "", "", "", "", phrase])
    for s in rapport.signalements:
        ecrivain.writerow(
            [neutraliser_formule(x) for x in (s.gravite.value, s.fichier, s.feuille)]
            + ["" if s.ligne is None else s.ligne]
            + [neutraliser_formule(s.colonne), neutraliser_formule(s.message)]
        )
    if rapport.signalements_omis:
        ecrivain.writerow(
            ["information", "", "", "", ""]
            + [
                f"{rapport.signalements_omis} autre(s) signalement(s) non détaillé(s), dont "
                f"{rapport.erreurs_omises} erreur(s) : limite de {MAX_SIGNALEMENTS} atteinte."
            ]
        )
    return tampon.getvalue().encode("utf-8-sig")
