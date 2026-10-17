import os
from dotenv import load_dotenv
load_dotenv(override=True)
from google import genai

key = os.getenv("LLM_API_KEY")
print("Key loaded:", bool(key), "| length:", len(key) if key else 0)

client = genai.Client(api_key=key)
for model in ["gemini-2.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-3.8-flash"]:
    try:
        r = client.models.generate_content(model=model, contents="Say hello")
        print("WORKS:", model)
    except Exception as e:
        print("FAILED:", model, "->", str(e)[:90])