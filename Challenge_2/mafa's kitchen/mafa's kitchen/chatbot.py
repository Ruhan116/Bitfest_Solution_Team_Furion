import os
import re
import google.generativeai as genai
from flask import Blueprint, request, jsonify
from db import get_db_connection

###############################################################################
# 1) SETUP: Configure Generative AI & Flask Blueprint
###############################################################################
os.environ["GOOGLE_API_KEY"] = "AIzaSyCjVfqk_C3ztvaNBrctDHM"
genai.configure(api_key=os.environ["GOOGLE_API_KEY"])

model = genai.GenerativeModel(model_name="gemini-1.5-flash")

chatbot_bp = Blueprint("chatbot_bp", __name__)

###############################################################################
# 2) UNIT CONVERSION & NORMALIZATION UTILS
###############################################################################
# Approximate conversion factors to grams for certain keywords
UNIT_MAP = {
    "g": 1.0,
    "gram": 1.0,
    "gram.": 1.0,  # in case of punctuation
    "grams": 1.0,
    "tbsp": 15.0,  # ~15g for 1 tablespoon (water-like density)
    "tablespoon": 15.0,
    "tsp": 5.0,
    "teaspoon": 5.0,
    "cup": 240.0,  # 1 cup ~ 240 ml => 240g if watery
    "cups": 240.0,
    "ml": 1.0,     # treat ml as ~ grams for water-based
    "ltr": 1000.0,
    "litre": 1000.0,
    "liter": 1000.0,
    "number": 1.0  # If your DB stores pieces as "number", we just approximate 1 piece = 1g (very rough!)
}

def parse_quantity_str(qty_str: str) -> float:
    """
    Convert a string like "250g", "2 tbsp", "1 cup" into an approximate float (grams).
    If no numeric value is found, default to 1.0. If no known unit is found, assume grams.
    """
    qty_str = qty_str.lower().strip()
    # 1) Extract the first numeric portion
    match = re.search(r"([\d\.]+)", qty_str)
    amount = float(match.group(1)) if match else 1.0

    # 2) Check if any known unit is in the string
    for key, factor in UNIT_MAP.items():
        if key in qty_str:
            return amount * factor

    # If no known unit is found, treat the entire amount as grams
    return amount


def normalize_name(name: str) -> str:
    """
    Simplify ingredient names by removing descriptors like 'boiled', 'minced', 'chopped'.
    Also remove trailing 's' (plural) and extra spaces, so 'boiled eggs' => 'egg'.
    """
    name = name.lower().strip()
    # Remove common descriptors
    descriptors = ["boiled", "minced", "chopped", "powder", "ground", 
                   "fresh", "dried", "sliced", "shredded", "optional"]
    for desc in descriptors:
        name = name.replace(desc, "")
    # Remove plural 's' if it ends with it
    if name.endswith("s"):
        name = name[:-1]
    return name.strip()

###############################################################################
# 3) FETCH FROM DB: Ingredients & Recipes
###############################################################################
def fetch_current_ingredients():
    """
    Returns a list of dicts like:
    [
      {"name": "Sugar", "quantity": 100.0, "unit": "gram"},
      {"name": "Egg", "quantity": 6.0, "unit": "number"},
      ...
    ]
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, quantity, unit FROM ingredients")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def fetch_recipes(taste=None):
    """
    Returns a list of recipe dicts from the 'recipes' table.
    If taste is specified (e.g., 'Sweet'), filter by that taste.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    if taste:
        cursor.execute("SELECT * FROM recipes WHERE taste = %s", (taste,))
    else:
        cursor.execute("SELECT * FROM recipes")
    recipes = cursor.fetchall()
    cursor.close()
    conn.close()
    return recipes


def fetch_recipe_ingredients(recipe_id):
    """
    Returns a list of ingredient rows for the given recipe_id.
    Each row looks like: {"item": "...", "quantity": "..."}
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT item, quantity
        FROM recipe_ingredients
        WHERE recipe_id = %s
    """, (recipe_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

###############################################################################
# 4) COMPUTE RECIPE FEASIBILITY / FITNESS
###############################################################################
def get_feasibility_score(recipe_id, user_ingredients):
    """
    Computes how well the user can make this recipe:
      - Convert everything to approximate grams
      - If a 'vital' ingredient is missing entirely, return 0.0 immediately
      - Otherwise, compute a ratio = (# of ingredients fully satisfied) / (total)
      - Return (score, missing_ingredients)

    'Vital' can be any set of items you consider essential. 
    If such an item is missing or drastically short, we skip the recipe.
    """
    VITAL_INGREDIENTS = {"egg", "flour", "milk", "pasta", "ramen noodle", "chicken", "sugar"}
    recipe_ings = fetch_recipe_ingredients(recipe_id)

    # Convert user's pantry to {normalized_name: grams}
    user_dict = {}
    for ing in user_ingredients:
        norm = normalize_name(ing["name"])
        user_qty_grams = parse_quantity_str(f"{ing['quantity']}{ing['unit']}")
        user_dict[norm] = user_dict.get(norm, 0.0) + user_qty_grams

    satisfied_count = 0
    needed_count = len(recipe_ings)
    missing_ings = []

    for r_ing in recipe_ings:
        norm_ing = normalize_name(r_ing["item"])  # e.g. "egg"
        needed_grams = parse_quantity_str(r_ing["quantity"])

        user_has = user_dict.get(norm_ing, 0.0)

        # If user doesn't have ANY of a vital ingredient, skip
        if norm_ing in VITAL_INGREDIENTS and user_has < 1e-9:
            # missing vital => immediate no
            return 0.0, [(norm_ing, needed_grams)]

        # If user has enough => satisfy
        if user_has >= needed_grams * 0.8:
            # We'll allow up to 20% shortage
            satisfied_count += 1
        else:
            shortfall = max(0.0, needed_grams - user_has)
            missing_ings.append((norm_ing, shortfall))

    # Compute ratio = how many are "satisfied" out of total
    score = (satisfied_count / needed_count) if needed_count > 0 else 1.0

    # If the score is too low (< 0.5, for instance), effectively 0.0 
    # so we skip. Adjust threshold as you like.
    if score < 0.5:
        return 0.0, missing_ings

    return score, missing_ings

###############################################################################
# 5) BUILD THE PROMPT
###############################################################################
def build_chat_prompt(user_input, user_ingredients, top_recipes):
    """
    Summarize user ingredients, 
    Summarize each recipe, its missing ingredients, and a 'feasibility' score.
    """
    # Summarize user ingredients in a text block
    user_ing_text = "\n".join([
        f"- {ing['name']}: {ing['quantity']} {ing['unit']}"
        for ing in user_ingredients
    ])

    recipes_text_blocks = []
    for (recipe, score, missing_ings) in top_recipes:
        # Summarize recipe's own ingredients
        recipe_ing = fetch_recipe_ingredients(recipe["recipe_id"])
        ing_list_str = ", ".join([
            f"{i['item']} ({i['quantity']})" for i in recipe_ing
        ])

        missing_str = ""
        if missing_ings:
            missing_str = "Missing or short ingredients: " + ", ".join([
                f"{name} (~{short:.1f}g short)" for (name, short) in missing_ings
            ])

        text_block = (
f"""
Recipe: {recipe['title']}
 - Taste: {recipe['taste']}
 - Cuisine: {recipe['cuisine']}
 - Prep Time: {recipe['prep_time']} minutes
 - Description: {recipe['description']}
 - Ingredients: {ing_list_str}
 - Instructions: {recipe['instructions']}
 - Fitness Score: {score:.2f}
 - {missing_str}
"""
        )
        recipes_text_blocks.append(text_block)

    all_recipes_str = "\n\n".join(recipes_text_blocks)

    prompt = f"""
You are an AI assistant that suggests recipes based on user preferences and available ingredients.

User's request: {user_input}

User currently has these ingredients (approx in grams):
{user_ing_text}

Here are some recipes that partly or fully match the user's ingredients:
{all_recipes_str}

Only recommend recipes with a good fitness score (above ~0.5) and 
avoid those missing vital ingredients entirely. Suggest how to handle small shortages.
Respond in a friendly and concise way.
"""
    return prompt.strip()

###############################################################################
# 6) CHAT ENDPOINT
###############################################################################
@chatbot_bp.route("/chat", methods=["POST"])
def chat():
    """
    Example JSON request body:
      {
        "user_input": "I want something sweet"
      }
    """
    data = request.get_json()
    user_input = data.get("user_input", "").strip()

    if not user_input:
        return jsonify({"assistant": "Hi, what can I help you cook today?"}), 200

    # Fetch user’s current ingredients
    user_ingredients = fetch_current_ingredients()

    # Simple check for 'sweet'
    is_sweet = "sweet" in user_input.lower()
    # Fetch recipes from DB that match taste if needed
    candidate_recipes = fetch_recipes(taste="Sweet" if is_sweet else None)

    # Evaluate feasibility (fitness score) for each recipe
    scored_recipes = []  # List of tuples: (recipe_dict, score, missing_ings)
    for recipe in candidate_recipes:
        score, missing_ings = get_feasibility_score(recipe["recipe_id"], user_ingredients)
        if score > 0.0:  # Only keep if not zero
            scored_recipes.append((recipe, score, missing_ings))

    # Sort by descending score
    scored_recipes.sort(key=lambda x: x[1], reverse=True)

    if not scored_recipes:
        # If nothing feasible
        return jsonify({"assistant": "I couldn't find any suitable recipes given your pantry."}), 200

    # Take the top 1–3
    top_recipes = scored_recipes[:3]

    # Build prompt for LLM
    prompt = build_chat_prompt(user_input, user_ingredients, top_recipes)
    response = model.generate_content(prompt)
    assistant_reply = response.text.strip()

    return jsonify({"assistant": assistant_reply}), 200
