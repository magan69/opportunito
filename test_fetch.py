from dotenv import load_dotenv
load_dotenv()

from tinyfish import TinyFish

client = TinyFish()

# Step 1: search
search_results = client.search.query(
    query="scholarships for engineering students India 2026",
    location="IN",
)

# Step 2: take the first 3 links
urls = [r.url for r in search_results.results[:3]]
print("Fetching:", urls)

# Step 3: read those pages
fetched = client.fetch.get_contents(urls=urls, format="markdown")

for page in fetched.results:
    print("\n=== ", page.title)
    print(page.text[:500])   # first 500 characters only

# Pages that failed show up here
for e in fetched.errors:
    print("FAILED:", e)