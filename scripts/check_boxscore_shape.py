import sys
sys.path.insert(0, ".")
from src.data.mlb_api import MLBStatsAPI

api = MLBStatsAPI(season=2026)
hitters, pitchers = api.get_game_boxscore_stats(824025)   # HOU @ LAA, 2026-06-08

print("type:", type(hitters))
print("n hitters:", len(hitters))
k = list(hitters)[:3]
print("sample keys:", k, [type(x) for x in k])
s = hitters[k[0]]
print("value type:", type(s))
print("fields:", [a for a in dir(s) if not a.startswith("_")])
print()
for pid in (545361, 670541, 572233):      # Trout, Alvarez, Walker
    st = hitters.get(pid)
    print(pid, st)
