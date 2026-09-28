import sqlite3

connection = sqlite3.connect("emploi_du_temps.db")
cursor = connection.cursor()

print("\n--- PROFESSEURS ---")
cursor.execute("SELECT * FROM professeur")
for professeur in cursor.fetchall():
    print(professeur)

print("\n--- MATIERES ---")
cursor.execute("SELECT * FROM matiere")
for matiere in cursor.fetchall():
    print(matiere)

print("\n--- CLASSES ---")
cursor.execute("SELECT * FROM classe")
for classe in cursor.fetchall():
    print(classe)

print("\n--- SALLES ---")
cursor.execute("SELECT * FROM salle")
for salle in cursor.fetchall():
    print(salle)

connection.close()