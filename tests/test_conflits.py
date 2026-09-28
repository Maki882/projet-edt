"""Tests unitaires et intégration SQLite, sans toucher à la base du projet."""

from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest

from verifier_conflits import Cours, detecter_conflits, verifier_base


class TestConflits(unittest.TestCase):
    def setUp(self):
        self.a = Cours(1, 1, 1, 1, "lundi", "08:30", "10:30")
        self.b = Cours(2, 2, 2, 2, "lundi", "09:30", "11:30")

    def test_ressources_distinctes(self):
        self.assertEqual(detecter_conflits([self.a, self.b]), [])

    def test_chaque_ressource_en_conflit(self):
        for ressource in ("professeur", "classe", "salle"):
            with self.subTest(ressource=ressource):
                b = replace(self.b, **{f"{ressource}_id": 1})
                conflits = detecter_conflits([self.a, b])
                self.assertEqual(len(conflits), 1)
                self.assertEqual(conflits[0].ressource, ressource)
                self.assertEqual((conflits[0].cours_a, conflits[0].cours_b), (1, 2))

    def test_jours_distincts(self):
        b = replace(self.a, id=2, jour="mardi")
        self.assertEqual(detecter_conflits([self.a, b]), [])

    def test_cours_contigus_sans_chevauchement(self):
        b = replace(self.a, id=2, heure_debut="10:30", heure_fin="12:30")
        self.assertEqual(detecter_conflits([self.a, b]), [])

    def test_inclusion_et_egalite_des_intervalles(self):
        for debut, fin in (("09:00", "10:00"), ("08:30", "10:30")):
            with self.subTest(debut=debut):
                b = replace(self.a, id=2, heure_debut=debut, heure_fin=fin)
                self.assertEqual(len(detecter_conflits([b, self.a])), 3)

    def test_entrees_invalides(self):
        for changements in (
            {"heure_debut": "25:00"}, {"heure_debut": "8h30"},
            {"heure_fin": "08:30"}, {"heure_fin": "07:00"},
            {"jour": " "}, {"salle_id": None},
        ):
            with self.subTest(changements=changements), self.assertRaises(ValueError):
                detecter_conflits([replace(self.a, **changements)])

    def test_identifiant_duplique(self):
        with self.assertRaises(ValueError):
            detecter_conflits([self.a, self.a])

    def test_planning_vide(self):
        self.assertEqual(detecter_conflits([]), [])

    def test_base_absente_non_creee(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = Path(dossier) / "absente.db"
            with self.assertRaises(sqlite3.OperationalError):
                verifier_base(chemin)
            self.assertFalse(chemin.exists())

    def test_integration_base_existante_non_modifiee(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = Path(dossier) / "planning.db"
            connexion = sqlite3.connect(chemin)
            try:
                connexion.execute(
                    "CREATE TABLE cours (id, professeur_id, classe_id, salle_id, "
                    "jour, heure_debut, heure_fin)"
                )
                connexion.executemany(
                    "INSERT INTO cours VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(1, 1, 1, 1, "lundi", "08:30", "10:30"),
                     (2, 1, 2, 2, "lundi", "09:30", "11:30")],
                )
                connexion.commit()
            finally:
                connexion.close()
            avant = chemin.read_bytes()
            self.assertEqual(len(verifier_base(chemin)), 1)
            self.assertEqual(chemin.read_bytes(), avant)
