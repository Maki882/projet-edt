import sqlite3

DATABASE_NAME = "emploi_du_temps.db"


def insert_data():
    connection = sqlite3.connect(DATABASE_NAME)
    cursor = connection.cursor()

    # Professeurs
    cursor.execute("""
        INSERT INTO professeur (nom, prenom, email)
        VALUES (?, ?, ?)
    """, ("Dupont", "Jean", "jean.dupont@ecole.fr"))

    cursor.execute("""
        INSERT INTO professeur (nom, prenom, email)
        VALUES (?, ?, ?)
    """, ("Martin", "Sarah", "sarah.martin@ecole.fr"))

    # Matières
    cursor.execute("""
        INSERT INTO matiere (nom)
        VALUES (?)
    """, ("Mathématiques",))

    cursor.execute("""
        INSERT INTO matiere (nom)
        VALUES (?)
    """, ("Informatique",))

    cursor.execute("""
        INSERT INTO matiere (nom)
        VALUES (?)
    """, ("Physique",))

    # Classes
    cursor.execute("""
        INSERT INTO classe (nom, niveau)
        VALUES (?, ?)
    """, ("SI2I-A", "2ème année"))

    cursor.execute("""
        INSERT INTO classe (nom, niveau)
        VALUES (?, ?)
    """, ("SI2I-B", "2ème année"))

    # Salles
    cursor.execute("""
        INSERT INTO salle (nom, capacite)
        VALUES (?, ?)
    """, ("B204", 30))

    cursor.execute("""
        INSERT INTO salle (nom, capacite)
        VALUES (?, ?)
    """, ("C105", 25))

    connection.commit()
    connection.close()

    print("Données ajoutées avec succès !")


if __name__ == "__main__":
    insert_data()