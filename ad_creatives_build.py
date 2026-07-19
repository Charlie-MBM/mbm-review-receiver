#!/usr/bin/env python3
"""MBM Meta ad creative generator (built 2026-07-18, session: dashboard/meta ops).
Rebuilds ALL campaign creatives deterministically from site repo images.
The generator is the source of truth; PNG/MP4 outputs are disposable.

Usage:  python3 ad_creatives_build.py <assets_dir> [outdir]
  assets_dir must contain: perimenopause-hero-1600.webp, home-hero-1600.webp,
  dr-scribner-headshot-1600.webp   (from mbm-rebuild-43f1acd5/dist/client/assets/heroes/)
Requires: Pillow, ffmpeg, fonts Lora-Variable.ttf + Poppins-*.ttf (google-fonts dir).

DESIGN RULES (do not casually change — each earned the hard way):
- Palette: forest #1F4A33 / cream #F6F2E9 / gold #9C6F10 (site + dashboard brand).
- On-image copy is informational third-person ONLY (Meta personal-attributes rule:
  never "you/your <condition>", no symptoms, no outcome claims). Brand gate:
  cash-pay, board-cert = Emergency Medicine only, no geo superlatives.
- Doctor layouts: photo circle TOP-CENTER, text BELOW (collision-free by
  construction; a text-over-photo overlap shipped once and Charlie caught it).
- Text coverage well under Meta's ~20% comfort zone.
"""
import os, sys, subprocess
from PIL import Image, ImageDraw, ImageFont

GREEN=(31,74,51); CREAM=(246,242,233); GOLD=(156,111,16); INK=(28,38,32); SAGE=(214,222,214)
FD="/usr/share/fonts/truetype/google-fonts"
LORA=f"{FD}/Lora-Variable.ttf"; POP_M=f"{FD}/Poppins-Medium.ttf"; POP_R=f"{FD}/Poppins-Regular.ttf"

def lora(sz,w=620):
    f=ImageFont.truetype(LORA,sz)
    try: f.set_variation_by_axes([w])
    except Exception: pass
    return f

def cover(im,w,h,focus=0.30,zoom=1.0):
    sw,sh=im.size; sc=max(w/sw,h/sh)*zoom
    im2=im.resize((int(sw*sc+.5),int(sh*sc+.5)),Image.LANCZOS)
    x=(im2.width-w)//2; y=int((im2.height-h)*focus)
    return im2.crop((x,y,x+w,y+h))

CAMPAIGNS={
 "A":dict(photo="perimenopause-hero-1600.webp",eyebrow="F R E E   G U I D E",
   l1="The Perimenopause",l2="Checklist",
   sub="Clear steps, told straight — from Mt. Baker Medical, Bellingham",
   focus_sq=0.28,focus_story=0.22),
 "C":dict(photo="home-hero-1600.webp",eyebrow="C O N C I E R G E   P R I M A R Y   C A R E",
   l1="Your doctor,",l2="actually available.",
   sub="Membership-based care · Mt. Baker Medical, Bellingham",
   focus_sq=0.12,focus_story=0.05),
}
URL="mtbakermedical.com"

def mix(col,bg,a): return tuple(int(c*(a/255)+bg[i]*(1-a/255)) for i,c in enumerate(col))

def band_layout(photo,cfg,W,H,bandH,scale,zoom=1.0,alphas=(255,)*5,story=False):
    img=Image.new("RGB",(W,H),GREEN)
    img.paste(cover(photo,W,H-bandH,cfg["focus_story" if story else "focus_sq"],zoom),(0,0))
    d=ImageDraw.Draw(img)
    d.rectangle([0,H-bandH,W,H],fill=GREEN); d.rectangle([0,H-bandH,W,H-bandH+int(6*scale)],fill=GOLD)
    eb=ImageFont.truetype(POP_M,int(25*scale)); hd=lora(int(62*scale)); sb=ImageFont.truetype(POP_R,int(26*scale)); ur=ImageFont.truetype(POP_M,int(23*scale))
    y=H-bandH+int(48*scale)
    for txt,f,col,gap,a in [(cfg["eyebrow"],eb,GOLD,16,alphas[0]),(cfg["l1"],hd,CREAM,2,alphas[1]),
        (cfg["l2"],hd,CREAM,20,alphas[2]),(cfg["sub"],sb,SAGE,14,alphas[3]),(URL,ur,GOLD,0,alphas[4])]:
        if a>0: d.text(((W-d.textlength(txt,font=f))//2,y),txt,font=f,fill=mix(col,GREEN,a))
        y+=f.size+int(gap*scale)
    return img

def doctor_layout(head,eyebrow,lines,subline,W=1080,H=1080):
    img=Image.new("RGB",(W,H),CREAM); d=ImageDraw.Draw(img)
    R=235; cx,cy=W//2,300
    mask=Image.new("L",(R*2,R*2),0); ImageDraw.Draw(mask).ellipse([0,0,R*2,R*2],fill=255)
    img.paste(cover(head,R*2,R*2,0.32),(cx-R,cy-R),mask)
    d.ellipse([cx-R,cy-R,cx+R,cy+R],outline=GREEN,width=6)
    eb=ImageFont.truetype(POP_M,25); hd=lora(60 if len(lines)<3 else 62); sb=ImageFont.truetype(POP_R,27); ur=ImageFont.truetype(POP_M,24)
    y=590
    def ct(txt,f,col,gap):
        nonlocal y
        d.text(((W-d.textlength(txt,font=f))//2,y),txt,font=f,fill=col); y+=f.size+gap
    ct(eyebrow,eb,GOLD,26)
    for i,ln in enumerate(lines): ct(ln,hd,GREEN,34 if i==len(lines)-1 else 8)
    ct(subline,sb,INK,22); ct(URL,ur,GOLD,0)
    return img

def motion(render_frame,out,frames=96,fps=24,tmp="._frames"):
    os.makedirs(tmp,exist_ok=True)
    for i in range(frames):
        p=i/(frames-1); zoom=1.0+0.06*p
        fade=lambda s: max(0,min(255,int(255*(p-s)/0.16)))
        render_frame(zoom,(fade(0.06),fade(0.16),fade(0.24),fade(0.38),fade(0.48))).save(f"{tmp}/f{i:03d}.jpg",quality=88)
    subprocess.run(["ffmpeg","-y","-loglevel","error","-framerate",str(fps),"-i",f"{tmp}/f%03d.jpg",
                    "-c:v","libx264","-pix_fmt","yuv420p","-crf","20",out],check=True)
    for f in os.listdir(tmp): os.remove(f"{tmp}/{f}")
    os.rmdir(tmp)

def main():
    assets=sys.argv[1]; out=sys.argv[2] if len(sys.argv)>2 else "."
    os.makedirs(out,exist_ok=True); os.chdir(out)
    head=Image.open(os.path.join(assets,"dr-scribner-headshot-1600.webp")).convert("RGB")
    for key,cfg in CAMPAIGNS.items():
        photo=Image.open(os.path.join(assets,cfg["photo"])).convert("RGB")
        band_layout(photo,cfg,1080,1080,400,1.0).save(f"{key}_square_1080.png")
        band_layout(photo,cfg,1080,1920,560,1.25,story=True).save(f"{key}_story_1080x1920.png")
        motion(lambda z,a,ph=photo,c=cfg: band_layout(ph,c,1080,1080,400,1.0,z,a),f"{key}_sq_motion.mp4")
        motion(lambda z,a,ph=photo,c=cfg: band_layout(ph,c,1080,1920,560,1.25,z,a,story=True),f"{key}_story_motion.mp4")
    doctor_layout(head,"F R E E   G U I D E",["The Perimenopause","Checklist"],
                  "from Mt. Baker Medical · Concierge primary care, Bellingham").save("B_square_1080.png")
    doctor_layout(head,"C O N C I E R G E   P R I M A R Y   C A R E",["Your doctor,","actually available."],
                  "Dr. James Scribner · Mt. Baker Medical, Bellingham").save("D_square_1080.png")
    print("all creatives rebuilt")

if __name__=="__main__": main()
