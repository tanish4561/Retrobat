import requests
from bs4 import BeautifulSoup

url = "https://romsfun.com/roms/"
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

try:
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.content, 'html.parser')
    
    # Try to find the grid
    # Usually these are in specific classes. I'll just print the first few relevant tags.
    # Looking for a list of games.
    
    games = soup.find_all('div', class_='item') # Common class name?
    if not games:
        games = soup.find_all('article')
    
    if not games:
         # Fallback: print body structure to guess
         print(soup.prettify()[:2000])
    else:
        print(f"Found {len(games)} games.")
        print(games[0].prettify())

except Exception as e:
    print(f"Error: {e}")
