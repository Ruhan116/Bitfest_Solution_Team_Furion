import MySQLdb
import MySQLdb.cursors
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')
MYSQL_DATABASE = "mofa_kitchen"


def get_db_connection():
    try:
        conn = MySQLdb.connect(
            host=MYSQL_HOST,
            user=MYSQL_USER,
            passwd=MYSQL_PASSWORD,
            database=MYSQL_DATABASE,
            cursorclass=MySQLdb.cursors.DictCursor
        )
        return conn
    except MySQLdb.Error as err:
        print(f"Error: Unable to connect to the database. {err}")
        raise
