"""Aides des tests du dépliage : la règle des semaines ouvertes écrite au plus simple."""

from __future__ import annotations

from edt.domaine.calendrier import Semaine


def semaines_ouvertes_de_reference(un_besoin, semaines, jours_fermes) -> tuple[Semaine, ...]:
    """La règle écrite au plus simple, jour par jour, avec `JourFerme.concerne`.

    C'est la définition (lente : une question par fermeture, par jour et par
    semaine). `semaines_ouvertes` doit toujours donner exactement ce résultat.
    """
    ouvertes = []
    for semaine in semaines:
        if un_besoin.date_debut is not None and semaine.dimanche < un_besoin.date_debut:
            continue
        if un_besoin.date_fin is not None and semaine.lundi > un_besoin.date_fin:
            continue
        entierement_fermee = all(
            any(
                ferme.concerne(
                    semaine.date_du(jour), un_besoin.groupes, ponctuelle=un_besoin.ponctuel
                )
                for ferme in jours_fermes
            )
            for jour in range(5)
        )
        if not entierement_fermee:
            ouvertes.append(semaine)
    return tuple(ouvertes)
