import requests
from bs4 import BeautifulSoup

url = "https://romsfun.com/roms/nintendo-ds/jet-impulse.html"
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

resp = requests.get(url, headers=headers)
soup = BeautifulSoup(resp.content, 'html.parser')

# Find download buttons
# Look for 'download' in text or href
links = soup.find_all('a', href=True)
for l in links:
    if 'download' in l.text.lower() or 'download' in l['href']:
        print(f"Download candidate: {l.text.strip()} -> {l['href']}")
        # print specific class
        print(f"Classes: {l.get('class')}")
