"""Présence des cas réalistes demandés, sur le jeu « grand » (le seul qui les couvre tous)."""

from __future__ import annotations

from sqlalchemy import select

from edt.db import modeles as m
from edt.domaine.enumerations import (
    CibleIndisponibilite,
    ModeVolume,
    NatureGroupe,
    NatureJourFerme,
    StatutEnseignant,
)


def _besoins(session, type_seance: str) -> list[m.Besoin]:
    return list(
        session.scalars(select(m.Besoin).join(m.TypeSeance).where(m.TypeSeance.code == type_seance))
    )


def test_aucun_besoin_ecarte_et_taille_attendue(jeu_grand):
    rapport = jeu_grand.rapport
    assert rapport.besoins_ecartes == ()
    assert all(550 <= n <= 900 for n in rapport.seances_par_semestre.values())
    assert set(rapport.besoins_par_mode) == {"par_semaine", "total", "ponctuel"}


def test_dst_a_creneau_impose_dont_un_hors_grille(jeu_grand):
    with jeu_grand.session() as session:
        debuts_grille = set(session.scalars(select(m.Creneau.debut_min)))
        creneaux = {
            (b.matiere.promotion.code, b.jour_impose, b.debut_impose_min)
            for b in _besoins(session, "DST")
        }
    assert creneaux == {("A1", 3, 13 * 60 + 45), ("A2", 0, 8 * 60)}
    assert 8 * 60 not in debuts_grille


def test_groupe_pmr_et_salles_accessibles(jeu_grand):
    with jeu_grand.session() as session:
        pmr = list(session.scalars(select(m.Groupe).where(m.Groupe.accessibilite_requise)))
        accessibles = {
            (s.campus.code, s.type_salle.code)
            for s in session.scalars(select(m.Salle))
            if s.accessibilite.value == "oui"
        }
    assert pmr and all(g.nature is NatureGroupe.DEMI_GROUPE for g in pmr)
    for type_salle in ("AMPHI", "COURS", "INFO", "TP", "LABO", "LANGUES", "PROJET"):
        assert ("NDC", type_salle) in accessibles and ("ISSY", type_salle) in accessibles


def test_tiers_temps_et_partiels_des_annees_3_a_5(jeu_grand):
    with jeu_grand.session() as session:
        fermetures = list(
            session.scalars(
                select(m.JourFerme).where(m.JourFerme.nature == NatureJourFerme.EXAMENS)
            )
        )
        examens = _besoins(session, "EXAMEN")
        tiers_temps = [b for b in examens if b.groupes[0].nature is NatureGroupe.TIERS_TEMPS]
        reservations = list(
            session.scalars(
                select(m.Indisponibilite).where(
                    m.Indisponibilite.cible == CibleIndisponibilite.SALLE,
                    m.Indisponibilite.motif.like("%partiels%"),
                )
            )
        )
        promotions_fermees = {
            f.groupe.code for f in fermetures if f.groupe.nature is NatureGroupe.PROMOTION
        }
        transverses_fermes = {
            f.groupe.code for f in fermetures if f.groupe.nature is NatureGroupe.TRANSVERSE
        }
        assert promotions_fermees == {"A3", "A4", "A5"}
        assert {"A3-ANG1", "A4-ANG1", "A5-ANG1"} <= transverses_fermes
        assert all(b.mode_volume is ModeVolume.PONCTUEL for b in examens)
        assert tiers_temps and reservations
        for tt in tiers_temps:
            classique = next(b for b in examens if b.matiere_id == tt.matiere_id and b is not tt)
            assert tt.duree_min > classique.duree_min
            assert tt.debut_impose_min == classique.debut_impose_min
        assert {b.matiere.promotion.code for b in examens} == {"A3", "A4", "A5"}


def test_apprentis_en_cycle_ingenieur(jeu_grand):
    with jeu_grand.session() as session:
        apprentis = [
            g
            for g in session.scalars(select(m.Groupe).where(m.Groupe.nature == NatureGroupe.CLASSE))
            if "-APP" in g.code
        ]
        with_besoins = {
            g.id for b in session.scalars(select(m.Besoin)) for g in b.groupes if "-APP" in g.code
        }
    assert {g.code.split("-")[0] for g in apprentis} == {"A3", "A4", "A5"}
    assert all(g.id in with_besoins for g in apprentis)


def test_site_voisin_et_trajets(jeu_grand):
    with jeu_grand.session() as session:
        campus = {c.code: c for c in session.scalars(select(m.Campus))}
        trajets = {
            frozenset((t.campus_a_id, t.campus_b_id)): t.duree_min
            for t in session.scalars(select(m.Trajet))
        }
        salles_stan = [s for s in session.scalars(select(m.Salle)) if s.campus.code == "STAN"]
    assert set(campus) == {"NDC", "ISSY", "STAN"}
    assert trajets[frozenset((campus["NDC"].id, campus["ISSY"].id))] == 60
    assert trajets[frozenset((campus["NDC"].id, campus["STAN"].id))] == 10
    assert salles_stan and all(s.code.startswith("Stan") for s in salles_stan)
    assert "Stan301" in {s.code for s in salles_stan}


def test_vacataire_peu_disponible_et_enseignant_fictif(jeu_grand):
    with jeu_grand.session() as session:
        enseignants = list(session.scalars(select(m.Enseignant)))
        indispos = list(
            session.scalars(
                select(m.Indisponibilite).where(
                    m.Indisponibilite.cible == CibleIndisponibilite.ENSEIGNANT
                )
            )
        )
        enseignants_besoins = {
            e.id for b in session.scalars(select(m.Besoin)) for e in b.enseignants
        }
    jours_bloques: dict[int, set[int]] = {}
    for i in indispos:
        if i.jour is not None and i.jour < 5 and (i.debut_min, i.fin_min) == (0, 1440):
            jours_bloques.setdefault(i.enseignant_id, set()).add(i.jour)
    vacataires = [e for e in enseignants if e.statut is StatutEnseignant.VACATAIRE and not e.fictif]
    assert any(
        len(jours_bloques.get(e.id, ())) >= 3 and e.id in enseignants_besoins for e in vacataires
    )
    assert any(e.fictif and e.id in enseignants_besoins for e in enseignants)
    assert any(i.date_debut is not None for i in indispos)
    assert any(e.max_minutes_semaine for e in enseignants)
    assert all(e.identifiant.startswith(("E", "FICTIF-")) for e in enseignants)


def test_calendrier_de_l_annee(jeu_grand):
    with jeu_grand.session() as session:
        fermetures = list(session.scalars(select(m.JourFerme)))
        sport = {
            i.groupe.code
            for i in session.scalars(
                select(m.Indisponibilite).where(
                    m.Indisponibilite.cible == CibleIndisponibilite.GROUPE
                )
            )
        }
        natures = {(f.nature, f.groupe.code) for f in fermetures if f.groupe is not None}
    globales = {
        f.libelle
        for f in fermetures
        if f.groupe_id is None and f.nature is NatureJourFerme.VACANCES
    }
    assert len(globales) == 4
    assert (NatureJourFerme.SEMAINE_SPECIALE, "A1") in natures
    assert (NatureJourFerme.AUTRE, "A4") in natures
    assert sport == {"A1", "A2"}


def test_langues_et_transverses(jeu_grand):
    with jeu_grand.session() as session:
        natures = {b.groupes[0].nature for b in _besoins(session, "LANGUE")}
        chevauchements = session.execute(select(m.groupe_chevauchement)).all()
        parcours = list(session.scalars(select(m.Groupe).where(m.Groupe.code.like("A%-P-%"))))
    assert natures == {NatureGroupe.TRANSVERSE, NatureGroupe.CLASSE}
    assert chevauchements and parcours
