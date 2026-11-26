import urllib.request, re, time
U=["https://www.dexerto.com/feed/","https://www.dexerto.com/cs2/feed/","https://esports.gg/feed/","https://esports.gg/news/counter-strike-2/feed/",
"https://www.esportsinsider.com/feed","https://www.gosugamers.net/counterstrike/news/rss","https://escorenews.com/en/rss","https://escorenews.com/rss/en/csgo",
"https://www.sheepesports.com/rss","https://www.sheepesports.com/feed","https://bo3.gg/news/rss","https://www.hotspawn.com/counter-strike/feed",
"https://dotesports.com/counter-strike/feed","https://www.thegamer.com/feed/category/counter-strike/","https://www.pcgamer.com/rss/","https://www.egamersworld.com/rss/news",
"https://www.vpesports.com/feed","https://www.dust2.us/rss","https://www.dust2.com.br/rss","https://www.strafe.com/rss/"]
for u in U:
  try:
    b=urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0 cs-prep-bot"}),timeout=20).read().decode("utf-8","replace")
    t=re.findall(r"<item>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",b,re.S)
    cs=[x for x in t if re.search(r"CS2|Counter-Strike|CS:GO|HLTV|Major|FURIA|MongolZ|Liquid|NAVI|Vitality",x,re.I)]
    print("OK",u,len(t),"items, CS:",len(cs),"|",cs[:3])
  except Exception as e: print("ERR",u,repr(e)[:100])
  time.sleep(1)
