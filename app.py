import os, time
from datetime import date, datetime
from typing import Optional
from dotenv import load_dotenv
load_dotenv()

import streamlit as st
from pydantic import BaseModel, Field
from google import genai
from tinyfish import TinyFish

# ---------- Data shape Gemini must return ----------
class Opportunity(BaseModel):
    is_opportunity: bool = Field(description="True only if this page describes a scholarship, internship, fellowship or competition students can apply to.")
    title: str = Field(description="Name of the opportunity.")
    deadline: Optional[str] = Field(description="Last date to apply as YYYY-MM-DD, or null if the page does not state one.")
    eligibility: str = Field(description="Who can apply, in one or two sentences.")
    apply_link: Optional[str] = Field(description="Link to apply, or null if not found.")
    match_score: int = Field(description="0-100: how well this fits the student profile.")
    match_reason: str = Field(description="One sentence explaining the score, mentioning any eligibility problem.")

tf = TinyFish()
gemini = genai.Client(api_key=os.getenv("LLM_API_KEY"))

def days_left(deadline):
    if not deadline:
        return None
    try:
        d = datetime.strptime(deadline, "%Y-%m-%d").date()
        return (d - date.today()).days
    except ValueError:
        return None

def run_pipeline(p, progress):
    # 1. SEARCH (TinyFish)
    queries = [
        f"{p['course']} scholarship India {p['category']} category 2026-27 apply now last date",
        f"scholarship for {p['year']} year {p['course']} students India 2026 apply online",
        f"{p['state']} state government scholarship {p['category']} students 2026-27 apply",
        f"{p['interests']} internship for {p['year']} year students India 2026 apply",
        f"{p['interests']} fellowship OR competition OR hackathon students India 2026 registration open",
    ]
    urls = []
    for q in queries:
        try:
            res = tf.search.query(query=q, location="IN")
        except Exception as e:
            print("Search failed:", q, e)
            continue
        for r in res.results[:5]:
            if r.url not in urls:
                urls.append(r.url)
    urls = urls[:14]
    progress.progress(0.2, text=f"TinyFish Search found {len(urls)} live pages. Reading them with TinyFish Fetch...")

    # 2. FETCH (TinyFish) - max 10 URLs per call, so fetch in batches
    all_pages = []
    for start in range(0, len(urls), 10):
        batch = urls[start:start + 10]
        try:
            fetched = tf.fetch.get_contents(urls=batch, format="markdown")
            all_pages.extend(fetched.results)
        except Exception as e:
            print("Fetch failed for a batch:", e)

    st.session_state["stats"] = {
        "searches": len(queries),
        "found": len(urls),
        "read": len(all_pages),
    }
    progress.progress(0.5, text="Analysing with Gemini...")

    # 3. EXTRACT + MATCH (Gemini)
    profile_text = (
        f"Course: {p['course']}; Year: {p['year']}; Category: {p['category']}; "
        f"State: {p['state']}; Family income per year: {p['income']}; Interests: {p['interests']}"
    )
    items = []
    total = max(len(all_pages), 1)
    for i, page in enumerate(all_pages):
        text = (page.text or "")[:15000]
        prompt = f"""Today's date is {date.today()}.
Extract the opportunity details from this web page and score how well it fits the student.
If the page is not a real opportunity students can apply to, set is_opportunity to false.
If eligibility clearly excludes the student (income, course, year, category), give a low score.
Only give a deadline if the page states one. Never guess.

STUDENT PROFILE: {profile_text}

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
                },
            )
            item = Opportunity.model_validate_json(response.text)
            data = item.model_dump()
            data["source_url"] = page.url
            items.append(data)
        except Exception as e:
            print("Skipped", page.url, "->", e)
        progress.progress(0.5 + 0.5 * (i + 1) / total, text=f"Analysed {i + 1} of {total} pages")
        time.sleep(2)  # stay within free limits

    # 4. Filter + rank
    good = []
    for it in items:
        d = days_left(it["deadline"])
        if it["is_opportunity"] and (d is None or d >= 0):
            it["days_left"] = d
            good.append(it)
    good = [g for g in good if g["match_score"] >= 20]
    good.sort(key=lambda x: x["match_score"], reverse=True)
    return good

# ---------- Web page ----------
st.set_page_config(page_title="Opportunito", page_icon="🎓")
st.title("🎓 Opportunito")
st.caption("Your career helper: finds live scholarships and opportunities that fit you, powered by TinyFish Search + Fetch and Gemini.")

with st.sidebar:
    st.header("Your profile")
    course = st.text_input("Course", "B.Tech Computer Science")
    year = st.selectbox("Year", ["1st", "2nd", "3rd", "4th"])
    category = st.selectbox("Category", ["General", "OBC", "SC", "ST", "EWS"])
    state = st.text_input("State", "Delhi")
    income = st.text_input("Family income per year", "Below 5 lakh")
    interests = st.text_input("Interests", "AI, software")
    go = st.button("Find opportunities", type="primary")

if go:
    profile = dict(course=course, year=year, category=category, state=state, income=income, interests=interests)
    progress = st.progress(0.0, text="Searching the live web with TinyFish...")
    try:
        st.session_state["results"] = run_pipeline(profile, progress)
    except Exception as e:
        st.error(f"Something went wrong: {e}")
    progress.empty()

stats = st.session_state.get("stats")
if stats:
    st.info(
        f"TinyFish ran {stats['searches']} live searches, found {stats['found']} pages "
        f"and read {stats['read']} of them for this result."
    )

results = st.session_state.get("results")
if results is not None:
    if not results:
        st.warning("No matching opportunities found. Try different interests or course wording.")
    for r in results:
        with st.container(border=True):
            st.subheader(r["title"])
            dl = r["days_left"]
            if dl is None:
                st.write(f"Match: **{r['match_score']}/100**  |  Deadline: not stated on page")
            else:
                st.write(f"Match: **{r['match_score']}/100**  |  Deadline: **{r['deadline']}** ({dl} days left)")
            st.write("**Who can apply:**", r["eligibility"])
            st.write("**Why this match:**", r["match_reason"])
            st.link_button("Open source page", r["source_url"])
            if r["apply_link"]:
                st.link_button("Apply link", r["apply_link"])