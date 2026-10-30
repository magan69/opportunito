import os, re, json, time, tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Optional
from dotenv import load_dotenv
load_dotenv()

import streamlit as st
from pydantic import BaseModel, Field
from google import genai
from tinyfish import TinyFish

# ---------- Data shape ----------
class Opportunity(BaseModel):
    is_opportunity: bool = Field(description="True if this describes a specific scholarship, internship, fellowship, competition or hackathon students can apply to.")
    title: str = Field(description="Name of the opportunity.")
    deadline: Optional[str] = Field(description="Last date as YYYY-MM-DD, or null if not stated.")
    eligibility: str = Field(description="Who can apply, in one or two sentences.")
    apply_link: Optional[str] = Field(description="Link to apply, or null.")
    match_score: int = Field(description="0-100 fit with the student profile.")
    match_reason: str = Field(description="One sentence explaining the score.")

tf = TinyFish()
gemini = genai.Client(api_key=os.getenv("LLM_API_KEY"))
MODEL = os.getenv("GEMINI_MODEL", "gemma-4-26b-a4b-it")
SAMPLE_FILE = "sample_results.json"

JSON_RULES = """Reply with ONLY one JSON object, no markdown and no extra text, with exactly these keys:
is_opportunity (true or false: a specific scholarship, internship, fellowship or competition students can apply to),
title (string),
deadline (YYYY-MM-DD, or null if not stated. Never guess),
eligibility (one or two sentences),
apply_link (string or null),
match_score (integer 0-100; low if the student clearly does not qualify),
match_reason (one sentence)."""

def days_left(deadline):
    if not deadline:
        return None
    try:
        d = datetime.strptime(deadline, "%Y-%m-%d").date()
        return (d - date.today()).days
    except ValueError:
        return None

def profile_to_text(p):
    return (
        f"Course: {p['course']}; Year: {p['year']}; Category: {p['category']}; "
        f"State: {p['state']}; Family income per year: {p['income']}; Interests: {p['interests']}"
    )

def search_one(query):
    try:
        res = tf.search.query(query=query, location="IN")
        return [r.url for r in res.results[:5]]
    except Exception as e:
        print("Search failed:", query, e)
        return []

def parse_item(raw_text):
    found = re.search(r"\{.*\}", raw_text, re.DOTALL)
    return Opportunity.model_validate_json(found.group(0))

def analyse_page(url, text, profile_text):
    """Ask Gemma 4 about one web page."""
    prompt = f"""Today's date is {date.today()}.
Read this web page and score how well it fits the student.
{JSON_RULES}

STUDENT PROFILE: {profile_text}

PAGE URL: {url}
PAGE TEXT:
{text}
"""
    last_error = None
    for attempt in range(3):
        try:
            response = gemini.models.generate_content(model=MODEL, contents=prompt)
            data = parse_item(response.text).model_dump()
            data["source_url"] = url
            return data, None
        except Exception as e:
            last_error = str(e)
            time.sleep(2 * (attempt + 1))
    return None, f"{url} -> {last_error[:200]}"

def analyse_image(uploaded, profile_text):
    """Ask Gemma 4 to read a photo or screenshot of a notice."""
    suffix = os.path.splitext(uploaded.name)[1] or ".png"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded.getvalue())
        path = tmp.name
    my_file = gemini.files.upload(file=path)
    prompt = f"""Today's date is {date.today()}.
This image is a photo or screenshot of a notice or poster. Read it and score how well it fits the student.
{JSON_RULES}

STUDENT PROFILE: {profile_text}
"""
    response = gemini.models.generate_content(model=MODEL, contents=[my_file, prompt])
    data = parse_item(response.text).model_dump()
    data["days_left"] = days_left(data["deadline"])
    data["source_url"] = None
    return data

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
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(search_one, queries))
    urls = []
    for url_list in results:
        for u in url_list:
            if u not in urls:
                urls.append(u)
    urls = urls[:6]
    progress.progress(0.25, text=f"TinyFish Search found {len(urls)} live pages. Reading them with TinyFish Fetch...")

    # 2. FETCH (TinyFish)
    all_pages = []
    try:
        fetched = tf.fetch.get_contents(urls=urls, format="markdown")
        all_pages = list(fetched.results)
    except Exception as e:
        print("Fetch failed:", e)
    progress.progress(0.5, text="Analysing with Gemma 4...")

    # 3. EXTRACT + MATCH (Gemma 4)
    profile_text = profile_to_text(p)
    items = []
    total = max(len(all_pages), 1)
    done = 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(analyse_page, pg.url, (pg.text or "")[:8000], profile_text)
            for pg in all_pages
        ]
        for f in as_completed(futures):
            data, err = f.result()
            if data is None:
                debug["errors"].append(err)
            else:
                items.append(data)
            done += 1
            progress.progress(0.5 + 0.5 * done / total, text=f"Analysed {done} of {total} pages")

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
        "searches": len(queries), "found": len(urls), "read": len(all_pages),
        "analysed": len(items), "shown": len(good),
    }
    st.session_state["debug"] = debug
    return good

def show_card(r):
    with st.container(border=True):
        st.subheader(r["title"])
        dl = r.get("days_left")
        if dl is None:
            st.write(f"Match: **{r['match_score']}/100**  |  Deadline: not stated")
        else:
            st.write(f"Match: **{r['match_score']}/100**  |  Deadline: **{r['deadline']}** ({dl} days left)")
        st.write("**Who can apply:**", r["eligibility"])
        st.write("**Why this match:**", r["match_reason"])
        if r.get("source_url"):
            st.link_button("Open source page", r["source_url"])
        if r.get("apply_link"):
            st.link_button("Apply link", r["apply_link"])

# ---------- Web page ----------
st.set_page_config(page_title="Opportunito", page_icon="🎓")
st.title("🎓 Opportunito")
st.caption("Your career helper: finds live scholarships and opportunities that fit you, powered by TinyFish Search + Fetch and Gemma 4 (open model).")

with st.sidebar:
    st.header("Your profile")
    course = st.text_input("Course", "B.Tech Computer Science")
    year = st.selectbox("Year", ["1st", "2nd", "3rd", "4th"])
    category = st.selectbox("Category", ["General", "OBC", "SC", "ST", "EWS"])
    state = st.text_input("State", "Delhi")
    income = st.text_input("Family income per year", "Below 5 lakh")
    interests = st.text_input("Interests", "AI, software")
    go = st.button("Find opportunities", type="primary")
    demo = st.button("Load saved example")

profile = dict(course=course, year=year, category=category, state=state, income=income, interests=interests)

if go:
    st.session_state["is_demo"] = False
    progress = st.progress(0.0, text="Searching the live web with TinyFish...")
    start_time = time.time()
    try:
        results_now = run_pipeline(profile, progress)
        st.session_state["results"] = results_now
        st.session_state["seconds"] = round(time.time() - start_time)
        if results_now:
            with open(SAMPLE_FILE, "w", encoding="utf-8") as f:
                json.dump(results_now, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.error(f"Something went wrong: {e}")
    progress.empty()
elif demo:
    if os.path.exists(SAMPLE_FILE):
        with open(SAMPLE_FILE, encoding="utf-8") as f:
            st.session_state["results"] = json.load(f)
        st.session_state["is_demo"] = True
        st.session_state.pop("stats", None)
        st.session_state.pop("debug", None)
    else:
        st.warning("No saved example yet. Run one live search first.")

if st.session_state.get("is_demo"):
    st.caption("Showing a saved example from a real live run, so you don't have to wait.")

stats = st.session_state.get("stats")
if stats:
    st.info(
        f"TinyFish ran {stats['searches']} live searches, found {stats['found']} pages and read {stats['read']}. "
        f"Gemma 4 analysed {stats['analysed']}, and {stats['shown']} matched you "
        f"(took {st.session_state.get('seconds', '?')} seconds)."
    )

debug = st.session_state.get("debug")
if debug:
    with st.expander("Debug info"):
        st.write(f"Not an opportunity: {debug['not_opportunity']}")
        st.write(f"Expired: {debug['expired']}")
        st.write(f"Low match (under 20): {debug['low_score']}")
        st.write(f"Model errors: {len(debug['errors'])}")
        for e in debug["errors"][:5]:
            st.code(e)

results = st.session_state.get("results")
if results is not None:
    if not results:
        st.warning("No matching opportunities found. Try different interests or course wording.")
    for r in results:
        show_card(r)

# ---------- Image upload (Gemma 4 multimodal) ----------
st.divider()
st.subheader("📷 Got a poster or WhatsApp notice?")
st.caption("Upload a photo or screenshot. Gemma 4 reads the image and checks if you qualify.")
img = st.file_uploader("Upload an image", type=["png", "jpg", "jpeg"])
if img is not None and st.button("Check this notice"):
    with st.spinner("Gemma 4 is reading the image..."):
        try:
            st.session_state["image_result"] = analyse_image(img, profile_to_text(profile))
        except Exception as e:
            st.session_state["image_result"] = None
            st.error(f"Could not read the image: {e}")

image_result = st.session_state.get("image_result")
if image_result:
    if image_result["is_opportunity"]:
        show_card(image_result)
    else:
        st.warning("This image does not look like a scholarship or opportunity notice.")