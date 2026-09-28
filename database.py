import sqlite3

DATABASE_NAME = "emploi_du_temps.db"


def get_connection():
    return sqlite3.connect(DATABASE_NAME)


def create_database():
    connection = get_connection()
    cursor = connection.cursor()

    # Table des professeurs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS professeur (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            prenom TEXT NOT NULL,
            email TEXT
        )
    """)

    # Table des classes
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS classe (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            niveau TEXT
        )
    """)

    # Table des salles
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS salle (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            capacite INTEGER
        )
    """)

    # Table des matières
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS matiere (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL
        )
    """)

    # Table des cours
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            matiere_id INTEGER NOT NULL,
            professeur_id INTEGER NOT NULL,
            classe_id INTEGER NOT NULL,
            salle_id INTEGER NOT NULL,
            jour TEXT NOT NULL,
            heure_debut TEXT NOT NULL,
            heure_fin TEXT NOT NULL,

            FOREIGN KEY (matiere_id) REFERENCES matiere(id),
            FOREIGN KEY (professeur_id) REFERENCES professeur(id),
            FOREIGN KEY (classe_id) REFERENCES classe(id),
            FOREIGN KEY (salle_id) REFERENCES salle(id)
        )
    """)

    connection.commit()
    connection.close()

    print("Base de données créée avec succès !")


if __name__ == "__main__":
    create_database()