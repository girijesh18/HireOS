import sqlite3

def add_name_column():
    conn = sqlite3.connect('swarm.db')
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE resume_versions ADD COLUMN name TEXT;")
        print("Successfully added name to resume_versions")
    except sqlite3.OperationalError as e:
        print(f"Error adding to resume_versions: {e}")
        
    try:
        cursor.execute("ALTER TABLE cover_letter_versions ADD COLUMN name TEXT;")
        print("Successfully added name to cover_letter_versions")
    except sqlite3.OperationalError as e:
        print(f"Error adding to cover_letter_versions: {e}")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    add_name_column()
