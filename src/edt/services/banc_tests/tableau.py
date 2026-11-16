"""Tableau d'une famille de tests, prêt à afficher : catalogue, historique et exécution réunis.

Pour chaque test, l'état affiché vient, dans cet ordre : du résultat rendu par
l'exécution en cours ; de « en cours » ou « en attente » s'il fait partie de
ce qu'elle lance ; du dernier résultat enregistré ; sinon « jamais lancé ».

Une exécution terminée n'est plus lue en mémoire : l'historique, qu'elle a
écrit avant de se dire terminée, fait foi. Ainsi les résultats enregistrés
ensuite par la commande `edt tests` (autre processus) s'affichent aussitôt.
Seul le dernier appel du suivi de cette exécution (`execution_suivie`) la
montre encore, pour dire comment elle s'est terminée.
Les comptes et les dates de dernière exécution complète sont donnés par
fichier, par composant et pour la famille.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field

from edt.services.banc_tests import execution as ex
from edt.services.banc_tests import resultats as res
from edt.services.banc_tests.catalogue import (
    DESCRIPTIONS_COMPOSANTS,
    FAMILLES,
    Catalogue,
    FichierCatalogue,
    TestCatalogue,
    cle_groupe_composant,
    cle_groupe_famille,
    cle_groupe_fichier,
    lire_catalogue,
)
from edt.services.banc_tests.execution import Suivi
from edt.services.banc_tests.resultats import BilanExecution, ResultatTest


def texte_duree(secondes: float | None) -> str:
    """DurÃ©e lisible : Â« 0,4 s Â», Â« 12 s Â», Â« 3 min 05 s Â»."""
    if secondes is None:
        return ""
    if secondes < 10:
        return f"{secondes:.1f} s".replace(".", ",")
    if secondes < 60:
        return f"{round(secondes)} s"
    minutes, reste = divmod(round(secondes), 60)
    return f"{minutes} min {reste:02d} s"


def identifiant_html(prefixe: str, cle: str) -> str:
    """Identifiant HTML stable et sûr pour une clé de test ou un chemin."""
    return f"{prefixe}-{hashlib.sha1(cle.encode('utf-8')).hexdigest()[:12]}"


@dataclass
class Compte:
    """Nombre de tests par état dans un groupe."""

    total: int = 0
    reussis: int = 0
    echecs: int = 0
    ignores: int = 0
    jamais: int = 0
    en_cours: int = 0
    charge: int = 0
    """Tests de charge (`perf`) du groupe, exclus des lancements par groupe."""

    def ajouter(self, ligne: Ligne) -> None:
        """Compte une ligne."""
        self.total += 1
        self.charge += ligne.test.perf
        if ligne.etat == res.REUSSI:
            self.reussis += 1
        elif ligne.etat == res.ECHEC:
            self.echecs += 1
        elif ligne.etat == res.IGNORE:
            self.ignores += 1
        elif ligne.etat in (res.EN_COURS, res.EN_ATTENTE):
            self.en_cours += 1
        else:
            self.jamais += 1


@dataclass
class Ligne:
    """Un test à afficher : son état et, s'il existe, son dernier résultat."""

    test: TestCatalogue
    etat: str
    resultat: ResultatTest | None
    id_html: str

    @property
    def libelle_etat(self) -> str:
        """Libellé de l'état (« Réussi », « Jamais lancé »…)."""
        return res.LIBELLES_ETATS[self.etat]


@dataclass
class FichierVue:
    """Un fichier de tests et ses lignes."""

    fichier: FichierCatalogue
    lignes: list[Ligne]
    compte: Compte
    complet_le: dt.datetime | None
    id_html: str


@dataclass
class ComposantVue:
    """Un composant d'une famille et ses fichiers."""

    nom: str
    description: str
    fichiers: list[FichierVue]
    compte: Compte
    complet_le: dt.datetime | None
    id_html: str

    @property
    def ouvert(self) -> bool:
        """Déplié d'office : il contient un échec ou un test en cours."""
        return bool(self.compte.echecs or self.compte.en_cours)


@dataclass
class TableauFamille:
    """Tout ce qu'affiche l'écran d'une famille de tests.

    - `suivi` : exécution de ce processus qui concerne cette famille (en cours
      ou dernière), sinon `None` ;
    - `ailleurs` : exécution menée par un autre processus (ligne de commande) ;
    - `indisponible` : pourquoi le lancement est désactivé, sinon `None` ;
    - `avertissement` : fichier des résultats illisible, sinon `None`.
    """

    famille: str
    titre: str
    catalogue: Catalogue
    composants: list[ComposantVue]
    compte: Compte
    complet_le: dt.datetime | None
    derniere: BilanExecution | None
    suivi: Suivi | None
    ailleurs: dict | None
    indisponible: str | None
    avertissement: str | None
    _lignes: dict[str, Ligne] = field(default_factory=dict, repr=False)

    def ligne(self, cle: str) -> Ligne | None:
        """Ligne du test `cle`, s'il est dans cette famille."""
        return self._lignes.get(cle)

    def composant_de(self, cle: str) -> ComposantVue | None:
        """Composant qui contient le test `cle` (s'il est dans cette famille)."""
        ligne = self._lignes.get(cle)
        if ligne is None:
            return None
        return next((c for c in self.composants if c.nom == ligne.test.composant), None)

    def fichier_de(self, cle: str) -> FichierVue | None:
        """Fichier qui contient le test `cle` (s'il est dans cette famille)."""
        composant = self.composant_de(cle)
        if composant is None:
            return None
        chemin = self._lignes[cle].test.fichier
        return next((f for f in composant.fichiers if f.fichier.chemin == chemin), None)


def _etat(
    test: TestCatalogue, suivi: Suivi | None, historique: res.Historique
) -> tuple[str, ResultatTest | None]:
    if suivi is not None and suivi.en_cours:
        recent = suivi.resultats.get(test.cle)
        if recent is not None:
            return recent.etat, recent
        if test.cle in suivi.tests_cible:
            precedent = historique.tests.get(test.cle)
            return (res.EN_COURS if test.cle == suivi.test_en_cours else res.EN_ATTENTE), precedent
    enregistre = historique.tests.get(test.cle)
    if enregistre is not None:
        return enregistre.etat, enregistre
    return res.JAMAIS, None


def _suivi_de_la_famille(famille: str, execution_suivie: str | None) -> Suivi | None:
    """Exécution de ce processus à montrer : en cours, ou terminée si c'est celle que suit l'appelant."""
    courante = ex.execution_courante()
    if courante is None:
        return None
    suivi = courante.suivi()
    if suivi.famille not in (None, famille):
        return None
    if not suivi.en_cours and suivi.id != execution_suivie:
        return None
    return suivi


def _fichiers_par_composant(
    fichiers: Iterable[FichierCatalogue],
) -> dict[str, list[FichierCatalogue]]:
    groupes: dict[str, list[FichierCatalogue]] = {}
    for fichier in fichiers:
        groupes.setdefault(fichier.composant, []).append(fichier)
    return dict(sorted(groupes.items()))


def tableau_famille(
    famille: str, catalogue: Catalogue | None = None, *, execution_suivie: str | None = None
) -> TableauFamille:
    """Le tableau de la famille `famille` (`unitaires` ou `integration`).

    `execution_suivie` : identifiant d'exécution que suit le fragment de suivi ;
    terminée, elle reste montrée à ce seul appelant.

    Ne lève pas pour un dépôt sans tests ou un historique illisible : l'écran
    affiche la raison. Une famille inconnue lève `KeyError` (l'appelant la
    vérifie avant).
    """
    titre = FAMILLES[famille]
    catalogue = catalogue or lire_catalogue()
    ailleurs = ex.execution_ailleurs()
    # Suivi d'abord, historique ensuite : une exécution vue terminée a déjà tout écrit.
    suivi = _suivi_de_la_famille(famille, execution_suivie)
    historique = res.lire_historique()

    lignes_par_cle: dict[str, Ligne] = {}
    composants: list[ComposantVue] = []
    compte_famille = Compte()
    for nom, fichiers in _fichiers_par_composant(catalogue.fichiers_de(famille)).items():
        compte_composant = Compte()
        vues: list[FichierVue] = []
        for fichier in fichiers:
            compte_fichier = Compte()
            lignes = []
            for test in fichier.tests:
                etat, resultat = _etat(test, suivi, historique)
                ligne = Ligne(test, etat, resultat, identifiant_html("t", test.cle))
                lignes.append(ligne)
                lignes_par_cle[test.cle] = ligne
                for compte in (compte_fichier, compte_composant, compte_famille):
                    compte.ajouter(ligne)
            vues.append(
                FichierVue(
                    fichier,
                    lignes,
                    compte_fichier,
                    historique.groupes.get(cle_groupe_fichier(fichier.chemin)),
                    identifiant_html("f", fichier.chemin),
                )
            )
        composants.append(
            ComposantVue(
                nom,
                DESCRIPTIONS_COMPOSANTS.get(nom, ""),
                vues,
                compte_composant,
                historique.groupes.get(cle_groupe_composant(famille, nom)),
                f"c-{famille}-{nom}",
            )
        )

    derniere = historique.derniere
    return TableauFamille(
        famille=famille,
        titre=titre,
        catalogue=catalogue,
        composants=composants,
        compte=compte_famille,
        complet_le=historique.groupes.get(cle_groupe_famille(famille)),
        derniere=derniere,
        suivi=suivi,
        ailleurs=ailleurs,
        indisponible=ex.raison_indisponible(catalogue),
        avertissement=historique.erreur,
        _lignes=lignes_par_cle,
    )
