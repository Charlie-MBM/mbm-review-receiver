import json, hashlib, sys, re

P="/sessions/serene-affectionate-mccarthy/mnt/mbm-review-receiver/dashboard_index.html"
s=open(P,encoding="utf-8").read()
orig=s
reps=[]
def R(label, old, new): reps.append((label, old, new))

# version bump: daily task already baked 27a today -> weekly layer becomes 27b
R("VER",'DATA_VERSION = "2026-07-27a"','DATA_VERSION = "2026-07-27b"')
R("baked_at",'baked_at: "2026-07-27T16:45Z"','baked_at: "2026-07-27T16:48Z"')

# weekly source as_of
R("src.ahrefs",'ahrefs: {as_of:"2026-07-23T11:53Z"','ahrefs: {as_of:"2026-07-27T16:20Z"')
R("src.meta",'meta:   {as_of:"2026-07-19T20:07Z"','meta:   {as_of:"2026-07-27T16:31Z"')

# meta_ads (now delivering)
R("meta_ads",
'''meta_ads: {spend:17.80, impressions:1662, clicks:86, lpv:81, window:"Jun 19–Jul 18 (trailing 30)",''',
'''meta_ads: {spend:193.60, impressions:15887, clicks:595, lpv:532, window:"Jun 27–Jul 26 (trailing 30)",''')
R("meta_warn",
'''warn:"Weekly Ads Manager pull 2026-07-19. 'Results' are LANDING-PAGE VIEWS, not leads — pixel has no lead event. Two campaigns in window: 'Perimenopause Checklist – Traffic' (active: $4.29 / 531 impr / 10 link clicks / 10 LPV) + the older promo (now OFF: $13.51 / 1,131 / 76 / 71). Trailing-30 window — excluded from MTD spend ratios."},''',
'''warn:"Weekly Ads Manager pull 2026-07-27. 'Results' are LANDING-PAGE VIEWS, not leads — pixel has no lead event. Campaigns now DELIVERING (prior 'rejected/frozen' state cleared). Three campaigns in window: 'MBM Perimenopause Checklist – Traffic' (active: $84.93 / 7,840 impr / 168 clicks / 156 LPV) + 'MBM Concierge – Traffic' (active: $44.23 / 2,931 / 152 / 140) + the 7/9 promo (completed, ended Jul 23: $64.44 / 5,116 / 275 / 236). Trailing-30 window — excluded from MTD spend ratios."},''')

# ahrefs DR / referring domains
R("ahrefs.rd",'ahrefs: {dr:0, ref_domains:309,','ahrefs: {dr:0, ref_domains:331,')
R("ahrefs.note",
'dr_note:"DR is 0 (rounded; was ~1) with ~309 referring domains (+~9 since last week) — still only ~7 dofollow. Yelp/BBB/Chamber citations are nofollow redirects; more of them never move DR. Only real dofollow links will. Read 2026-07-23 from the Ahrefs web UI."',
'dr_note:"DR is 0 (rounded; was ~1) with ~331 referring domains (+~31 since last week) — still only ~7 dofollow. Yelp/BBB/Chamber citations are nofollow redirects; more of them never move DR. Only real dofollow links will. Read 2026-07-27 from the Ahrefs web UI."')

# GBP attribution (W4)
R("gbp_attrib",
'SNAPSHOT.gbp_attrib = {sessions:27, users:23, taps:4, handoffs:1, as_of:"2026-07-22"};',
'SNAPSHOT.gbp_attrib = {sessions:36, users:29, taps:4, handoffs:1, as_of:"2026-07-27"};')

# channel_econ: meta spend/window/note, gbp directions note, nextdoor taps+note
R("ce.meta",
'{ch:"Meta (paid social)", spend:17.80, window:"Jun 19–Jul 18 trailing", taps:37, chan_people:6, note:"weekly Ads Manager pull 2026-07-19: $17.80 across 2 campaigns (one active, one now off). Trailing-30 window ≠ MTD; treat $/person as approximate. LPV, not leads.", warn:true},',
'{ch:"Meta (paid social)", spend:193.60, window:"Jun 27–Jul 26 trailing", taps:37, chan_people:6, note:"weekly Ads Manager pull 2026-07-27: $193.60 across 3 delivering campaigns (prior frozen state cleared). Trailing-30 window ≠ MTD; treat $/person as approximate. LPV, not leads.", warn:true},')
R("ce.gbp",
'note:"UTM-attributed; badly undercounts — 15 profile CALLS + 52 direction requests this month bypass the site entirely"},',
'note:"UTM-attributed; badly undercounts — 15 profile CALLS + 60 direction requests this month bypass the site entirely"},')
R("ce.nd",
'{ch:"Nextdoor", spend:118.58, window:"since Jul 8 launch", taps:5, chan_people:0, note:"Refreshed 2026-07-19: $118.58 / 4,277 impr / 50 clicks since Jul 8 launch. UTM tags now LIVE - GA4 shows 5 nextdoor/paid sessions MTD; no pixel so chan_people unknown."}',
'{ch:"Nextdoor", spend:118.58, window:"since Jul 8 launch", taps:30, chan_people:0, note:"Spend/impr/clicks held from 07-20 (Ads Manager stuck loading 07-27). UTM tags LIVE - GA4 shows 30 nextdoor/paid sessions MTD (up from 5); no pixel so chan_people unknown."}')

missing=[]
for label, old, new in reps:
    c=s.count(old)
    if c!=1: missing.append((label,c))
    else: s=s.replace(old,new,1)

# KW regeneration (weekly ranks from Rank Tracker API)
a=s.find("const KW=")
b=s.find("};",a)+2
KW=json.loads(s[a+len("const KW="):b-1])
newp={
 "menopause doctor bellingham":29,"perimenopause doctor bellingham":28,"same day doctor bellingham":19,
 "bioidentical hormone replacement therapy bellingham":17,"bellingham primary care":16,
 "depression treatment bellingham":15,"hrt clinic near me":15,"medical weight loss bellingham":15,
 "mens health bellingham":15,"gender affirming care bellingham":13,"iv therapy bellingham":10,
 "perimenopause treatment bellingham":10,"weight loss injections bellingham":10,"functional medicine bellingham":9,
 "hrt near me":9,"functional medicine":8,"testosterone clinic bellingham":6,"weight loss clinic near me":6,
 "direct primary care bellingham":5,"ketamine therapy near me":5,"longevity doctor near me":5,"trt bellingham":5,
 "testosterone replacement therapy bellingham":4,"walk-in visits bellingham":4,"concierge doctor bellingham":3,
 "dexa scan near me":3,"ed treatment bellingham":3,"bellingham concierge medicine":2,"glp-1 bellingham":2,
 "ketamine therapy bellingham":2,"concierge primary care":1,"concierge primary care bellingham":1,
 "dr james scribner bellingham":1,"longevity medicine bellingham":1,"mt baker medical":1,
 "anxiety treatment bellingham":None,"iv therapy near me":None,"urgent care bellingham":None,
 "urgent care near me":None,"walk in clinic near me":None,"walk-in clinic bellingham":None,"walk-in doctor bellingham":None,
}
kwmiss=[k for k in KW if k not in newp]
newKW={}
for k,v in KW.items():
    np=newp.get(k); op=v.get("p")
    d=(op-np) if (op is not None and np is not None) else None
    newKW[k]={"t":v["t"],"r":v["r"],"p":np,"kd":v.get("kd"),"v":v.get("v"),"d":d}
s=s[:a]+"const KW="+json.dumps(newKW,ensure_ascii=False,separators=(",",":"))+";"+s[b:]

if missing or kwmiss:
    print("ABORT unmatched:",missing," kwmiss:",kwmiss); sys.exit(1)

open(P,"w",encoding="utf-8").write(s)
print("WROTE",len(s),"bytes (was",len(orig),")")
print("sha256",hashlib.sha256(s.encode()).hexdigest())
print("ends_html",s.rstrip().endswith("</html>"))
print("VER",re.search(r'DATA_VERSION = \"([^\"]+)\"',s).group(1))
print("reps",len(reps),"KW",len(newKW),"ranked",sum(1 for v in newKW.values() if v['p'] is not None))
