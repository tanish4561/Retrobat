import requests
from bs4 import BeautifulSoup

url = "https://romsfun.com/roms/nintendo-ds/"
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

resp = requests.get(url, headers=headers)
soup = BeautifulSoup(resp.content, 'html.parser')

links = soup.find_all('a', href=True)
count = 0
for l in links:
    if '.html' in l['href'] and '/roms/' in l['href']: 
        # Find the image container
        img_container = l.find_parent('div', class_=lambda x: x and 'aspect-' in x)
        if img_container:
             # The card is likely the parent of the image container
             card = img_container.parent
             if card:
                 print("-" * 40)
                 print(card.prettify()[:1500])
                 count += 1
                 if count >= 2: break
