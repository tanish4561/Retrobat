import requests
from bs4 import BeautifulSoup

url = "https://romsfun.com/roms/nintendo-ds/"
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

resp = requests.get(url, headers=headers)
soup = BeautifulSoup(resp.content, 'html.parser')

# Dump all classes found on divs to see what structure uses
classes = set()
for d in soup.find_all('div', class_=True):
    for c in d['class']:
        classes.add(c)
print(f"Classes found: {list(classes)[:20]}")

# Look for links that look like game pages
links = soup.find_all('a', href=True)
count = 0
for l in links:
    if '/roms/' in l['href'] and count < 3:
        # Check if it has an image inside
        img = l.find('img')
        if img:
            print(f"Game Link: {l['href']}")
            print(f"Title: {l.get('title', l.text.strip())}")
            print(f"Image: {img.get('src') or img.get('data-src')}")
            # print parent container class
            print(f"Parent Class: {l.parent.get('class')}")
            count += 1
