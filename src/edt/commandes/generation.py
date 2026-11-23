"""Commandes de génération : `generer`, `pre-verifier`, `instancier`, `diagnostiquer`.

    python -m edt pre-verifier --semestre 1
    python -m edt pre-verifier --niveau PAUSE=facultatif  # mêmes options de règles que generer
    python -m edt generer --regle R007=facultatif --sans-regle R009   # règles personnalisées
    python -m edt generer --semestre 1 --temps-limite 120 --optimiser --graine 3
    python -m edt generer -d EQUIPEMENT -d PAUSE          # contraintes ignorées
    python -m edt generer --niveau PAUSE=facultatif       # niveau d'une contrainte
    python -m edt instancier --version 4
    python -m edt diagnostiquer 12 --temps 120       # règles et données en cause

`generer` lance un calcul par le même service que l'interface
(`edt.services.generation`), attend sa fin et affiche le résultat : en cas de
succès, une nouvelle version d'emploi du temps est enregistrée, contrôlée par
le vérificateur, et ses séances datées sont produites. `instancier` refait
seulement les séances datées d'une version existante. `diagnostiquer` cherche,
pour un calcul impossible, un plus petit ensemble de règles et de données qui
le rend impossible (`edt.services.diagnostic`), l'enregistre avec le calcul et
l'affiche.

Une interruption au clavier (Ctrl+C) pendant `generer` arrête le calcul
proprement : s'il a déjà trouvé un emploi du temps valide, celui-ci est
enregistré. Un calcul dont le processus a été tué ne bloque pas les suivants :
il est clos au prochain lancement.

Codes de sortie : `0` si tout a réussi ; `1` si le calcul n'aboutit pas
(impossible, temps dépassé, arrêté, refusé) ou, pour `pre-verifier`, si une
impossibilité certaine est relevée ; `2` pour une erreur inattendue. Pour
`diagnostiquer` : `0` dès qu'un diagnostic est rendu, quel que soit son
statut ; `1` si le diagnostic est refusé (calcul introuvable ou pas
impossible, durée hors limites, calcul en cours).
"""

from __future__ import annotations

import dataclasses
from typing import Annotated

import typer

from edt.db.modeles import Semestre
from edt.db.moteur import identifiant_valide, session_base, transaction_immediate
from edt.domaine.contraintes import (
    CATALOGUE,
    Niveau,
    Niveaux,
    contrainte,
    niveau,
    niveaux_depuis_desactivees,
    normaliser_niveaux,
)
from edt.domaine.enumerations import Gravite
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.services import diagnostic, generation
from edt.services.calendrier import semestre_par_defaut
from edt.services.emploi_du_temps import presentation
from edt.services.instanciation import RapportInstanciation, instancier_version
from edt.services.parametres import lire_parametres
from edt.services.regles import charger_regles_actives
from edt.services.verification import version_a_verifier
from edt.solveur.contrat import OptionsSolveur

commandes = typer.Typer()

CODE_ECHEC = 1

OptionSemestre = Annotated[
    int | None,
    typer.Option("--semestre", help="Identifiant du semestre (défaut : le semestre en cours)."),
]
OptionDesactiver = Annotated[
    list[str] | None,
    typer.Option(
        "--desactiver",
        "-d",
        help="Code d'une contrainte à ignorer, par exemple PAUSE (option répétable).",
    ),
]
OptionNiveau = Annotated[
    list[str] | None,
    typer.Option(
        "--niveau",
        help="Niveau d'une contrainte, CODE=niveau (necessaire, important ou facultatif), "
        "par exemple PAUSE=facultatif (option répétable).",
    ),
]


OptionRegle = Annotated[
    list[str] | None,
    typer.Option(
        "--regle",
        help="Niveau d'une règle personnalisée pour ce calcul, CODE=niveau, par exemple "
        "R007=facultatif (option répétable).",
    ),
]
OptionSansRegle = Annotated[
    list[str] | None,
    typer.Option(
        "--sans-regle",
        help="Code d'une règle personnalisée à ne pas appliquer à ce calcul (option répétable).",
    ),
]


def niveaux_regles_choisis(
    session, semestre: Semestre, regle: list[str] | None, sans_regle: list[str] | None
) -> dict[int, Niveau] | None:
    """Niveaux des règles personnalisées d'après la ligne de commande, ou `None` sans option.

    Les règles actives de l'année du semestre, chacune à son niveau ; `--sans-regle`
    en retire, `--regle CODE=niveau` en change le niveau.
    """
    if not regle and not sans_regle:
        return None
    actives = charger_regles_actives(session, semestre.annee_scolaire_id)
    par_code = {r.code.upper(): r for r in actives if r.code and r.id is not None}
    niveaux: dict[int, Niveau] = {r.id: r.niveau for r in par_code.values()}

    def trouver(code: str):
        # Espaces et casse tolérés : « r003 », « R003 ».
        entree = par_code.get(code.strip().upper())
        if entree is None:
            raise ErreurDonneesInvalides(
                f"Règle personnalisée inconnue ou inactive : {code.strip()!r} (règles actives : "
                + (", ".join(sorted(r.code for r in par_code.values())) or "aucune")
                + ")."
            )
        return entree

    for code in sans_regle or ():
        niveaux.pop(trouver(code).id, None)
    for choix in regle or ():
        code, separateur, valeur = (morceau.strip() for morceau in choix.partition("="))
        if not separateur or not code or not valeur:
            raise ErreurDonneesInvalides(
                f"Option --regle : écrire CODE=niveau, par exemple R007=facultatif (lu : {choix!r})."
            )
        entree = trouver(code)
        if entree.id not in niveaux:
            raise ErreurDonneesInvalides(
                f"La règle {entree.code} est à la fois ignorée (--sans-regle) et dotée d'un "
                "niveau (--regle) : choisir."
            )
        niveaux[entree.id] = niveau(valeur.lower())
    return niveaux


def _recapitulatif_des_regles(
    session, semestre: Semestre, niveaux: dict[int, Niveau] | None
) -> str | None:
    """Les règles dont le niveau change pour ce calcul, et celles ignorées."""
    if niveaux is None:
        return None
    actives = charger_regles_actives(session, semestre.annee_scolaire_id)
    changements = [
        f"{r.code} : {niveaux[r.id].libelle.lower()}"
        for r in actives
        if r.id in niveaux and niveaux[r.id] is not r.niveau
    ]
    changements += [f"{r.code} : ignorée" for r in actives if r.id not in niveaux]
    return "Règles personnalisées : " + " ; ".join(changements) + "." if changements else None


def niveaux_choisis(desactiver: list[str] | None, niveau: list[str] | None) -> Niveaux:
    """Niveaux d'un calcul d'après la ligne de commande : codes ignorés, puis niveaux choisis.

    Casse et espaces tolérés, comme pour `--regle` : « -d pause », « --niveau " Pause =
    Facultatif " ».
    """
    niveaux = niveaux_depuis_desactivees(code.strip().upper() for code in desactiver or ())
    for choix in niveau or ():
        code, separateur, valeur = (morceau.strip() for morceau in choix.partition("="))
        code, valeur = code.upper(), valeur.lower()
        if not separateur or not code or not valeur:
            raise ErreurDonneesInvalides(
                "Option --niveau : écrire CODE=niveau, par exemple PAUSE=facultatif "
                f"(lu : {choix!r})."
            )
        entree = contrainte(code)
        if entree.code not in niveaux:
            raise ErreurDonneesInvalides(
                f"La contrainte {entree.code} est à la fois ignorée (--desactiver) et dotée d'un "
                "niveau (--niveau) : choisir."
            )
        niveaux[entree.code] = normaliser_niveaux({entree.code: valeur})[entree.code]
    return niveaux


def _recapitulatif_des_niveaux(niveaux: Niveaux) -> str | None:
    """Les contraintes dont le niveau n'est pas celui par défaut, et celles ignorées."""
    changements = [
        f"{entree.libelle} : {niveaux[code].libelle.lower()}"
        for code, entree in CATALOGUE.items()
        if code in niveaux and niveaux[code] is not entree.niveau_defaut
    ]
    changements += [
        f"{entree.libelle} : ignorée" for code, entree in CATALOGUE.items() if code not in niveaux
    ]
    return "Règles : " + " ; ".join(changements) + "." if changements else None


def _semestre(session, semestre_id: int | None) -> Semestre:
    """Le semestre demandé, ou celui proposé par défaut."""
    if semestre_id is None:
        return semestre_par_defaut(session)
    # Un identifiant hors des bornes de SQLite (2**63) ferait lever le pilote : il n'existe pas.
    semestre = session.get(Semestre, semestre_id) if identifiant_valide(semestre_id) else None
    if semestre is None:
        raise ErreurIntrouvable(f"Le semestre {semestre_id} n'existe pas.")
    return semestre


@commandes.command("pre-verifier")
def pre_verifier(
    semestre: OptionSemestre = None,
    desactiver: OptionDesactiver = None,
    niveau: OptionNiveau = None,
    regle: OptionRegle = None,
    sans_regle: OptionSansRegle = None,
) -> None:
    """Dit, avant tout calcul, ce qui rend un emploi du temps impossible ou difficile."""
    niveaux = niveaux_choisis(desactiver, niveau)
    with session_base() as session:
        cible = _semestre(session, semestre)
        libelle = cible.libelle
        niveaux_regles = niveaux_regles_choisis(session, cible, regle, sans_regle)
        rappel_regles = _recapitulatif_des_regles(session, cible, niveaux_regles)
        anomalies = generation.pre_verifier_semestre(
            session, cible.id, niveaux=niveaux, niveaux_regles=niveaux_regles
        )
    for rappel in (_recapitulatif_des_niveaux(niveaux), rappel_regles):
        if rappel:
            typer.echo(rappel)
    erreurs = [a for a in anomalies if a.gravite is Gravite.ERREUR]
    alertes = [a for a in anomalies if a.gravite is Gravite.AVERTISSEMENT]
    typer.echo(f"Pré-vérifications du semestre « {libelle} » :")
    for titre, liste in (("Impossibilités", erreurs), ("Avertissements", alertes)):
        if liste:
            typer.echo(f"{titre} ({len(liste)}) :")
            for anomalie in liste:
                typer.echo(f"  - [{anomalie.code.value}] {anomalie.message}")
    if erreurs:
        typer.echo("Résultat : le calcul serait refusé.")
        raise typer.Exit(CODE_ECHEC)
    typer.echo(
        "Résultat : rien n'empêche de lancer le calcul"
        + (f" ({len(alertes)} avertissement(s))." if alertes else ".")
    )


@commandes.command("generer")
def generer(
    semestre: OptionSemestre = None,
    temps_limite: Annotated[
        float | None,
        typer.Option(
            "--temps-limite", help="Durée maximale du calcul, en secondes (défaut : paramètre)."
        ),
    ] = None,
    graine: Annotated[
        int, typer.Option("--graine", help="Graine : une autre graine donne une autre solution.")
    ] = 0,
    optimiser: Annotated[
        bool | None,
        typer.Option(
            "--optimiser/--sans-optimiser",
            help="Minimiser trous, changements de campus, samedi et créneaux extrêmes "
            "(défaut : paramètre « objectif_actif »).",
        ),
    ] = None,
    desactiver: OptionDesactiver = None,
    niveau: OptionNiveau = None,
    regle: OptionRegle = None,
    sans_regle: OptionSansRegle = None,
    fils: Annotated[
        int | None,
        typer.Option("--fils", help="Fils de calcul (défaut : paramètre ; 0 = au plus deux)."),
    ] = None,
    sans_indices: Annotated[
        bool,
        typer.Option("--sans-indices", help="Ne pas partir de l'emploi du temps actif."),
    ] = False,
    tolerer: Annotated[
        bool,
        typer.Option(
            "--tolerer-violations-heritees",
            help="Accepter des séances verrouillées qui se contredisent déjà entre elles.",
        ),
    ] = False,
    sans_activer: Annotated[
        bool,
        typer.Option("--sans-activer", help="Enregistrer la version sans la rendre active."),
    ] = False,
) -> None:
    """Génère l'emploi du temps d'un semestre et attend le résultat (code 1 si le calcul échoue)."""
    with session_base() as session:
        cible = _semestre(session, semestre)
        semestre_id, libelle = cible.id, cible.libelle
        niveaux_regles = niveaux_regles_choisis(session, cible, regle, sans_regle)
        rappel_regles = _recapitulatif_des_regles(session, cible, niveaux_regles)
        options = OptionsSolveur.depuis_parametres(
            lire_parametres(session),
            niveaux=niveaux_choisis(desactiver, niveau),
            graine=graine,
            tolerer_violations_heritees=tolerer,
            niveaux_regles=niveaux_regles,
        )
    changements: dict[str, object] = {"utiliser_indices": not sans_indices}
    if temps_limite is not None:
        changements["temps_limite_s"] = temps_limite
    if optimiser is not None:
        changements["optimiser"] = optimiser
    if fils is not None:
        changements["nb_workers"] = fils
    options = dataclasses.replace(options, **changements)

    typer.echo(
        f"Génération de l'emploi du temps du semestre « {libelle} » "
        f"(temps limite {options.temps_limite_s:g} s, "
        f"{'avec' if options.optimiser else 'sans'} optimisation)..."
    )
    for rappel in (_recapitulatif_des_niveaux(options.niveaux), rappel_regles):
        if rappel:
            typer.echo(rappel)
    identifiant = generation.lancer_calcul(semestre_id, options, activer=not sans_activer)
    try:
        suivi = generation.attendre_calcul(identifiant)
    except KeyboardInterrupt:
        # Ctrl+C : le calcul s'arrête proprement au lieu de rester « en cours » en base.
        typer.echo(
            "Interruption demandée : le calcul s'arrête (un emploi du temps déjà trouvé est "
            "conservé)..."
        )
        generation.demander_arret(identifiant)
        suivi = generation.attendre_calcul(identifiant)

    typer.echo(f"Calcul n° {suivi.id} : {suivi.libelle} en {suivi.temps_ecoule_s:g} s.")
    typer.echo(suivi.message)
    for avertissement in suivi.avertissements:
        typer.echo(f"  Avertissement : {avertissement}")
    indicateurs = suivi.statistiques.get("indicateurs")
    if indicateurs and "trous" not in suivi.statistiques:
        # Sans amélioration, le message du calcul ne dit rien de la qualité : elle est donnée ici.
        typer.echo(
            f"Bilan sur l'ensemble du semestre : {indicateurs.get('trous_creneaux')} créneau(x) "
            f"libre(s) entre deux cours dans les journées des groupes et "
            f"{indicateurs.get('seances_samedi')} séance(s) le samedi."
        )
    if suivi.fichier_journal:
        typer.echo(f"Journal du calcul : {suivi.fichier_journal}")
    generation.exiger_reussite(suivi)
    typer.echo(f"Version enregistrée : n° {suivi.version_id}.")


@commandes.command("instancier")
def instancier(
    version: Annotated[
        int | None, typer.Option("--version", help="Identifiant de la version à instancier.")
    ] = None,
    semestre: Annotated[
        int | None,
        typer.Option("--semestre", help="Identifiant du semestre (sa version active)."),
    ] = None,
) -> None:
    """Produit les séances datées d'une version et replace celles qui tombent un jour fermé."""
    with session_base() as session:
        # Lit la version puis remplace ses séances datées : verrou d'écriture pris avant la
        # première lecture (voir `edt.services.instanciation`).
        transaction_immediate(session)
        version_id = version_a_verifier(session, version_id=version, semestre_id=semestre)
        rapport = instancier_version(session, version_id)
    for ligne in lignes_du_rapport(rapport):
        typer.echo(ligne)


LIBELLES_STATUT_DIAGNOSTIC = {
    diagnostic.StatutDiagnostic.MINIMAL: "Ensemble minimal trouvé",
    diagnostic.StatutDiagnostic.PARTIEL: "Ensemble trouvé, peut-être pas le plus petit",
    diagnostic.StatutDiagnostic.POSSIBLE: "Le calcul n'est plus impossible",
    diagnostic.StatutDiagnostic.INDETERMINE: "Diagnostic sans conclusion",
}
"""Titre de chaque statut de diagnostic : les mêmes que sur la page du calcul
(`edt.web.routes.calculs.LIBELLES_STATUT_DIAGNOSTIC`)."""

RAPPEL_REGLES_GARDEES = (
    "Les règles qui définissent le problème sont toujours gardées : une seule séance à la fois "
    "par salle, par enseignant et par groupe, toutes les séances placées, séances verrouillées "
    "et jours fermés respectés."
)

AUCUN_CALCUL = (
    "Aucun calcul n'a encore été lancé : lancer d'abord un calcul (« python -m edt generer »), "
    "puis le diagnostiquer s'il est impossible."
)


@commandes.command("diagnostiquer")
def diagnostiquer(
    calcul_id: Annotated[
        int | None,
        typer.Argument(
            metavar="[CALCUL_ID]",
            help="Numéro du calcul impossible à diagnostiquer (défaut : le dernier calcul).",
        ),
    ] = None,
    temps: Annotated[
        float,
        typer.Option(
            "--temps",
            help=f"Durée maximale du diagnostic, en secondes, de {diagnostic.BUDGET_MIN_S:g} à "
            f"{diagnostic.BUDGET_MAX_S:g}.",
        ),
    ] = diagnostic.BUDGET_COMMANDE_S,
) -> None:
    """Désigne les règles et les données qui, ensemble, rendent un calcul impossible."""
    if calcul_id is None:
        dernier = generation.dernier_calcul()
        if dernier is None:
            raise ErreurIntrouvable(AUCUN_CALCUL)
        calcul_id = dernier.id
    if diagnostic.BUDGET_MIN_S <= temps <= diagnostic.BUDGET_MAX_S:
        # Hors bornes (NaN, infini compris), le service refuse : rien à annoncer.
        typer.echo(
            f"Recherche des règles et des données en cause du calcul n° {calcul_id} "
            f"(au plus {temps:g} s)..."
        )
    # La durée hors limites, le calcul introuvable ou pas impossible : refus du service (code 1).
    resultat = diagnostic.diagnostiquer_calcul(calcul_id, temps)
    for ligne in lignes_du_diagnostic(resultat):
        typer.echo(ligne)


def lignes_du_diagnostic(resultat: diagnostic.Diagnostic) -> list[str]:
    """Texte lisible d'un diagnostic : le même contenu que la page du calcul."""
    statut = LIBELLES_STATUT_DIAGNOSTIC.get(resultat.statut, str(resultat.statut))
    lignes = [f"Diagnostic du calcul n° {resultat.calcul_id} : {statut}", resultat.message]
    if resultat.statut is diagnostic.StatutDiagnostic.PARTIEL:
        partiel = (
            "Cet ensemble rend bien le calcul impossible, mais il n'est peut-être pas le plus "
            "petit : le temps a manqué pour le vérifier."
        )
        if resultat.budget_s < diagnostic.BUDGET_MAX_S:
            partiel += (
                " Pour aller plus loin, lancer « python -m edt diagnostiquer "
                f"{resultat.calcul_id} --temps {diagnostic.BUDGET_MAX_S:g} »."
            )
        lignes.append(partiel)
    if resultat.statut in (
        diagnostic.StatutDiagnostic.MINIMAL,
        diagnostic.StatutDiagnostic.PARTIEL,
    ):
        lignes.append("Règles en cause :")
        lignes.extend(f"  - {element.libelle}" for element in resultat.regles)
        if not resultat.regles:
            lignes.append("  Aucune règle en cause en plus des règles qui définissent le problème.")
        lignes.append("Données en cause :")
        lignes.extend(f"  - {element.libelle}" for element in resultat.donnees)
        if not resultat.donnees:
            lignes.append(
                "  Aucune donnée en cause : la règle suffit à rendre le calcul impossible."
            )
        lignes.append(RAPPEL_REGLES_GARDEES)
    lignes.append(diagnostic.AVERTISSEMENT_DONNEES_ACTUELLES)
    lignes.append(
        f"{presentation.pluriel(resultat.nb_essais, 'essai')} en "
        f"{presentation.duree_secondes(resultat.duree_s)} "
        f"(au plus {presentation.duree_secondes(resultat.budget_s)})."
    )
    return lignes


def lignes_du_rapport(rapport: RapportInstanciation) -> list[str]:
    """Texte lisible d'un rapport d'instanciation."""
    lignes = [f"Version n° {rapport.version_id} : {rapport.resume()}"]
    if rapport.nb_conservees:
        lignes.append(
            f"{rapport.nb_conservees} séance(s) saisie(s) à la main ou importée(s) laissée(s) "
            "en place."
        )
    if rapport.a_programmer:
        lignes.append(f"À programmer ({rapport.nb_a_programmer}) :")
        lignes.extend(
            f"  - {entree.besoin}, prévue le {entree.date_origine:%d/%m/%Y} ({entree.horaire}) : "
            f"{entree.raison}"
            for entree in rapport.a_programmer
        )
    return lignes
