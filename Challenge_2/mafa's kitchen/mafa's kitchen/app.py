import MySQLdb
import os
import json
from dotenv import load_dotenv
import sys
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from flask import Flask, render_template, request, redirect, url_for, session
from authlib.integrations.flask_client import OAuth
from functools import wraps
from db import get_db_connection
from decimal import Decimal
import requests
from chatbot import chatbot_bp


# Load environment variables
load_dotenv()


app = Flask(__name__)
app.register_blueprint(chatbot_bp, url_prefix="/chatbot")
app.secret_key = os.getenv('SECRET_KEY')

MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')

# MySQL connection
db = MySQLdb.connect(
    host=MYSQL_HOST,
    user=MYSQL_USER,
    passwd=MYSQL_PASSWORD,
    database=MYSQL_DATABASE,
    cursorclass=MySQLdb.cursors.DictCursor
)

def create_ingredients_table():

    cursor = db.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ingredients (
            ingredient_id INT NOT NULL AUTO_INCREMENT,
            name VARCHAR(255) NOT NULL,
            quantity DECIMAL(10,2) NOT NULL DEFAULT 0,
            unit ENUM('gram','litre','number') NOT NULL DEFAULT 'gram',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP 
                        ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (ingredient_id)
        );
    """)
    
    db.commit()
    cursor.close()
    print("Table 'ingredients' created or already exists.")

def create_recipes_tables():
    """
    Creates the 'recipes' and 'recipe_ingredients' tables if they do not exist.
    """
    cursor = db.cursor()
    
    # Create 'recipes' table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recipes (
            recipe_id INT NOT NULL AUTO_INCREMENT,
            title VARCHAR(255),
            taste VARCHAR(100),
            cuisine VARCHAR(100),
            prep_time INT,
            description TEXT,
            instructions TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(recipe_id)
        );
    """)

    # Create 'recipe_ingredients' table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recipe_ingredients (
            id INT NOT NULL AUTO_INCREMENT,
            recipe_id INT NOT NULL,
            item VARCHAR(255),
            quantity VARCHAR(100),
            PRIMARY KEY(id),
            FOREIGN KEY (recipe_id) REFERENCES recipes(recipe_id) ON DELETE CASCADE
        );
    """)

    db.commit()
    cursor.close()
    print("Tables 'recipes' and 'recipe_ingredients' created (if not exist).")


def load_structured_recipes_into_db(json_file="structured_recipes.json"):
    """
    1) Reads the structured JSON from the specified file.
    2) Inserts each recipe into the 'recipes' table.
    3) Inserts each ingredient into 'recipe_ingredients' table.
    """
    # Read the JSON file
    if not os.path.exists(json_file):
        print(f"Error: {json_file} not found.")
        return

    with open(json_file, "r", encoding="utf-8") as f:
        recipes_data = json.load(f)
    
    cursor = db.cursor()

    # Loop over each recipe in the JSON list
    for recipe in recipes_data:
        # Extract fields from JSON
        title = recipe.get("title", "")
        taste = recipe.get("taste", "")
        cuisine = recipe.get("cuisine", "")
        prep_time = recipe.get("prep_time", 0)
        description = recipe.get("description", "")
        
        # instructions can be either a list or string; unify to a single string
        instructions_data = recipe.get("instructions", "")
        if isinstance(instructions_data, list):
            instructions_str = "\n".join(instructions_data)
        else:
            instructions_str = instructions_data

        # Insert into 'recipes' table
        insert_recipe_sql = """
            INSERT INTO recipes (title, taste, cuisine, prep_time, description, instructions)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        cursor.execute(insert_recipe_sql, (
            title, taste, cuisine, prep_time,
            description, instructions_str
        ))
        db.commit()
        
        # Get the inserted recipe_id
        recipe_id = cursor.lastrowid
        
        # Insert each ingredient into 'recipe_ingredients'
        # The JSON might store them in recipe["ingredients"], typically a list of dicts
        ingredients_data = recipe.get("ingredients", [])
        
        # Some recipes might store ingredients as a list of dicts or strings
        # e.g. a dict: {"item": "...", "quantity": "..."} 
        # or in some entries we see just a string. We'll handle both:
        if isinstance(ingredients_data, list):
            for ing in ingredients_data:
                # if it's a dict with "item" and "quantity"
                if isinstance(ing, dict):
                    item_name = ing.get("item") or ing.get("name") or "Unnamed"
                    quantity = ing.get("quantity", "")
                else:
                    # if it's just a string
                    item_name = str(ing)
                    quantity = ""
                
                insert_ingredient_sql = """
                    INSERT INTO recipe_ingredients (recipe_id, item, quantity)
                    VALUES (%s, %s, %s)
                """
                cursor.execute(insert_ingredient_sql, (recipe_id, item_name, quantity))
                db.commit()

        else:
            # If 'ingredients' is not a list, you can decide how to handle it
            # For now, ignore or log a warning
            print(f"Warning: 'ingredients' was not a list for recipe '{title}'. Skipped.")
    
    # Close up
    cursor.close()
    print(f"Imported {len(recipes_data)} recipes from {json_file}.")

@app.route('/')
def home():
    """
    Display a simple homepage with:
    - A table of current ingredients.
    - Forms to add (shop) or reduce (cook) ingredients.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ingredients")
    ingredients_list = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return render_template("index.html", ingredients=ingredients_list)


@app.route('/add_ingredient', methods=['POST'])
def add_ingredient():
    """
    Shopping: Add or update ingredient quantity. 
    If the ingredient exists, increase its quantity; otherwise, create a new row.
    """
    name = request.form.get('name')
    quantity = request.form.get('quantity', 0)
    unit = request.form.get('unit', 'gram')

    try:
        quantity = float(quantity)
    except ValueError:
        quantity = 0.0

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if ingredient already exists
    cursor.execute("SELECT ingredient_id, quantity, unit FROM ingredients WHERE name = %s", (name,))
    row = cursor.fetchone()
    
    if row:
        # Update quantity
        new_qty = row['quantity'] + quantity
        # Optional: If you want to preserve old unit, you can keep row['unit'] 
        #           but typically you'd override with whatever user selected.
        cursor.execute("""
            UPDATE ingredients
            SET quantity = %s,
                unit = %s
            WHERE ingredient_id = %s
        """, (new_qty, unit, row['ingredient_id']))
    else:
        # Insert new ingredient
        cursor.execute("""
            INSERT INTO ingredients(name, quantity, unit)
            VALUES (%s, %s, %s)
        """, (name, quantity, unit))
    
    conn.commit()
    cursor.close()
    conn.close()

    # Redirect back to home to see the updated list
    return redirect(url_for('home'))


@app.route('/cook', methods=['POST'])
def cook():
    """
    Cooking: Decrease ingredient quantity based on the user input.
    If there’s not enough quantity, we’ll just set it to 0 (or you can handle otherwise).
    """
    name = request.form.get('name')
    quantity_str = request.form.get('quantity', '0')

    try:
        quantity_decimal = Decimal(quantity_str)
    except ValueError:
        quantity_decimal = Decimal('0')

    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT ingredient_id, quantity FROM ingredients WHERE name = %s", (name,))
    row = cursor.fetchone()
    if row:
        current_qty = row['quantity']
        new_qty = current_qty - quantity_decimal
        if new_qty < 0:
            new_qty = 0  # or raise an error, if desired
        
        cursor.execute("""
            UPDATE ingredients
            SET quantity = %s
            WHERE ingredient_id = %s
        """, (new_qty, row['ingredient_id']))
        conn.commit()
    
    cursor.close()
    conn.close()
    
    return redirect(url_for('home'))

@app.route("/chat_ui")
def chat_ui():
    """
    Renders a simple HTML page with a text input to communicate 
    with the /chatbot/chat endpoint via JavaScript fetch.
    """
    return render_template("chat.html")

if __name__ == "__main__":
    create_ingredients_table()
    create_recipes_tables()      
    load_structured_recipes_into_db("structured_recipes.json")

    app.run(debug=True, port=5000)



