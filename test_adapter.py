from sources.romsfun_adapter import RomsFunAdapter

adapter = RomsFunAdapter()
print("Testing search...")
games = adapter.search_games()
print(f"Found {len(games)} games.")
if games:
    print("First game:", games[0])
else:
    print("No games found. Inspecting HTML might be needed.")
