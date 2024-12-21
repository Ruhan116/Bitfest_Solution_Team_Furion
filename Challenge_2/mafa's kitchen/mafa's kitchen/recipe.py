import os
import json
import google.generativeai as genai

# 1) Configure your Google PaLM / Generative AI key
os.environ["GOOGLE_API_KEY"] = "AIzaSyCjVfqk_C3ztvaNBrctDHM-Ccmuop5Cq1U"  # or load from .env, etc.
genai.configure(api_key=os.environ["GOOGLE_API_KEY"])

# 2) Create a GenerativeModel instance for Gemini
model = genai.GenerativeModel(model_name="gemini-1.5-flash")


def parse_single_recipe_with_genai(unstructured_text: str):
    """
    Calls Google Generative AI (Gemini) to parse the unstructured text
    into a structured JSON object containing fields:
      title, taste, cuisine, prep_time, description, ingredients, instructions.

    Returns a Python dict if successful, or None if parsing fails.
    """
    prompt = f"""You are a helpful AI that extracts structured data from a casual, unstructured recipe.
The user wrote:
---
{unstructured_text}
---

strictly return valid JSON(no code fences, no markdown) with the following format and add quantities to each ingredients mentioning gram, litres or numbers(no extra text):

{{
  "title": "...",
  "taste": "...",
  "cuisine": "...",
  "prep_time": 0,
  "description": "...",
  "ingredients": "...",
  "instructions": "..."
}}

If any information is missing, must use an appropriate data. Make sure the final output is valid JSON only.
"""

    try:
        # Use model.generate_content(...) to get the model's response
        response = model.generate_content(prompt)
        
        # Get the text output from the first generation
        raw_output = response.text.strip()

        # Attempt to parse the output as JSON
        try:
            structured_data = json.loads(raw_output)
            return structured_data
        except json.JSONDecodeError as e:
            print(f"Failed to parse model output as JSON:\n{raw_output}\nError: {e}")
            return None

    except Exception as e:
        print(f"Error calling Google Generative AI: {e}")
        return None


def genai_parse_all_recipes():
    """
    1. Scan a 'recipes/' folder for .txt files (unstructured recipes).
    2. For each file, read the text and parse it with Google Generative AI (Gemini).
    3. Collect the structured JSON for all recipes.
    4. Write them to 'structured_recipes.json'.
    """
    recipes_folder = "recipes"  # Adjust if your folder name differs
    all_structured_recipes = []

    for filename in os.listdir(recipes_folder):
        if filename.endswith(".txt"):
            file_path = os.path.join(recipes_folder, filename)
            with open(file_path, "r", encoding="utf-8") as f:
                unstructured_text = f.read()

            structured_recipe = parse_single_recipe_with_genai(unstructured_text)
            if structured_recipe:
                all_structured_recipes.append(structured_recipe)
            else:
                print(f"Warning: Could not parse {filename} into structured JSON.")

    # Write all structured recipes into a single JSON file
    output_file = "structured_recipes.json"
    with open(output_file, "w", encoding="utf-8") as out:
        json.dump(all_structured_recipes, out, indent=2, ensure_ascii=False)

    print(f"Done! {len(all_structured_recipes)} recipes saved to {output_file}.")


# Optional: if you want to run it directly:
if __name__ == "__main__":
    genai_parse_all_recipes()
