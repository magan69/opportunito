from dotenv import load_dotenv
load_dotenv()

from tinyfish import TinyFish

client = TinyFish()

response = client.search.query(
    query="scholarships for engineering students India 2026",
    location="IN",
)

for r in response.results:
    print(r.title, "->", r.url)