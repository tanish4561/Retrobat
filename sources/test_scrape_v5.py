import requests
from bs4 import BeautifulSoup

url = "https://romsfun.com/download/jet-impulse-116561"
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

resp = requests.get(url, headers=headers)
soup = BeautifulSoup(resp.content, 'html.parser')

# Check links on the download page
links = soup.find_all('a', href=True)
for l in links:
    # Often "click here if download doesnt start"
    if 'download' in l.text.lower() or 'click here' in l.text.lower():
         print(f"Final candidate: {l.text.strip()} -> {l['href']}")
    # Sometimes it's a hidden form or JS.
    # But usually romsfun has a direct link after a wait.
    
    # Check for anything that ends in .zip, .7z, .rar, .nds, .gba etc
    if l['href'].lower().endswith(('.zip', '.rar', '.7z', '.nds', '.gba', '.iso')):
         print(f"Direct link found: {l['href']}")
