import os, time, json
from typing import Optional
from dotenv import load_dotenv
load_dotenv()

from pydantic import BaseModel, Field
from google import genai
from tinyfish import TinyFish

# ---- Define the shape of the data we want back ----
class Opportunity(BaseModel):
    is_opportunity: bool = Field(description="True only if this page describes a scholarship, internship, fellowship or competition students can apply to.")
    title: str = Field(description="Name of the opportunity.")
    deadline: Optional[str] = Field(description="Last date to apply as YYYY-MM-DD, or null if not stated.")
    eligibility: str = Field(description="Who can apply, in one or two sentences.")
    apply_link: Optional[str] = Field(description="Link to apply, or null if not found.")

# ---- Set up both services ----
tf = TinyFish()
gemini = genai.Client(api_key=os.getenv("LLM_API_KEY"))

# ---- 1. Search ----
results = tf.search.query(
    query="scholarships for engineering students India 2026",
    location="IN",
)
urls = [r.url for r in results.results[:3]]

# ---- 2. Fetch ----
pages = tf.fetch.get_contents(urls=urls, format="markdown")

# ---- 3. Extract with Gemini ----
for page in pages.results:
    text = (page.text or "")[:8000]   # keep it short
    prompt = f"""Extract the details from this web page.
If it is not a real opportunity students can apply to, set is_opportunity to false.

PAGE URL: {page.url}
PAGE TEXT:
{text}
"""
    try:
        response = gemini.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
                        config={
                "response_mime_type": "application/json",
                "response_schema": Opportunity,
            },)
        item = Opportunity.model_validate_json(response.text)
        print("\n-----")
        print(item.model_dump_json(indent=2))
    except Exception as e:
        print("Could not process", page.url, "->", e)

    time.sleep(2)  # small pause to stay within free limits