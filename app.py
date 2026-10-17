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
    is_opportunity: bool = Field(description="True if this page describes a specific scholarship, internship, fellowship, competition or hackathon that students can apply to. List pages that name several opportunities with details count too.")
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

def analyse_page(prompt):
    """Ask Gemini, and retry up to 3 times if it says 'too many requests'."""
    last_error = None
    for attempt in range(3):
        try:
            response = gemini.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": Opportunity,
                },
            )
            return Opportunity.model_validate_json(response.text), None
        except Exception as e:
            last_error = str(e)
            time.sleep(8 * (attempt + 1))  # wait longer each time
    return None, last_error

def run_pipeline(p, progress):
    debug = {"errors": [], "not_opportunity": 0, "expired": 0, "low_score": 0}

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
    progress.progress(0.5, text="Analysing with Gemini...")

    # 3. EXTRACT + MATCH (Gemini)
    profile_text = (
        f"Course: {p['course']}; Year: {p['year']}; Category: {p['category']}; "
        f"State: {p['state']}; Family income per year: {p['income']}; Interests: {p['interests']}"
    )
    items = []
    total = max(len(all_pages), 1)
    for i, page in enumerate(all_pages):
        text = (page.text or "")[:12000]
        prompt = f"""Today's date is {date.today()}.
Extract the opportunity details from this web page and score how well it fits the student.
If the page is not about an opportunity students can apply to, set is_opportunity to false.
If eligibility clearly excludes the student (income, course, year, category), give a low score.
Only give a deadline if the page states one. Never guess.

STUDENT PROFILE: {profile_text}

PAGE URL: {page.url}
PAGE TEXT:
{text}
"""
        item, err = analyse_page(prompt)
        if item is None:
            debug["errors"].append(f"{page.url} -> {err[:200]}")
        else:
            data = item.model_dump()
            data["source_url"] = page.url
            items.append(data)
        progress.progress(0.5 + 0.5 * (i + 1) / total, text=f"Analysed {i + 1} of {total} pages")
        time.sleep(1.5)

    # 4. Filter + rank
    good = []
    for it in items:
        d = days_left(it["deadline"])
        if not it["is_opportunity"]:
            debug["not_opportunity"] += 1
        elif d is not None and d < 0:
            debug["expired"] += 1
        elif it["match_score"] < 20:
            debug["low_score"] += 1
        else:
            it["days_left"] = d
            good.append(it)
    good.sort(key=lambda x: x["match_score"], reverse=True)

    st.session_state["stats"] = {
        "searches": len(queries),
        "found": len(urls),
        "read": len(all_pages),
        "analysed": len(items),
        "shown": len(good),
    }
    st.session_state["debug"] = debug
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
        f"TinyFish ran {stats['searches']} live searches, found {stats['found']} pages and read {stats['read']}. "
        f"Gemini analysed {stats['analysed']}, and {stats['shown']} matched you."
    )

debug = st.session_state.get("debug")
if debug:
    with st.expander("Debug info"):
        st.write(f"Not an opportunity: {debug['not_opportunity']}")
        st.write(f"Expired: {debug['expired']}")
        st.write(f"Low match (under 20): {debug['low_score']}")
        st.write(f"Gemini errors: {len(debug['errors'])}")
        for e in debug["errors"][:5]:
            st.code(e)

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