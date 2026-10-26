"""Compose existing Ghostty terminal layers with the original Herdr mark.
Run with Pillow installed. Does not change application assets.
"""
from pathlib import Path
from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageDraw
import base64
import io
import re

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
ASSETS = ROOT / 'ghostty/macos/Assets.xcassets/Custom Icon'

def layer(folder, name):
    return Image.open(ASSETS / (folder + '.imageset') / name).convert('RGBA')

def data(im):
    stream = io.BytesIO()
    im.save(stream, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()

base = layer('CustomIconBaseAluminum', 'base.png')
screen = layer('CustomIconScreen', 'screen-dark.png')
mask = layer('CustomIconScreenMask', 'screen-mask.png').getchannel('A')
gloss = layer('CustomIconGloss', 'gloss.png')
crt = layer('CustomIconCRT', 'crt-effect.png')
# Blue phosphor screen with the original inset shading.
gradient = Image.new('RGBA', (1024, 1024))
pixels = gradient.load()
for y in range(1024):
    t = min(1, max(0, (y - 173) / 677))
    c = tuple(round(a + (b-a)*t) for a,b in zip((14, 29, 66), (11, 24, 158)))
    for x in range(1024):
        pixels[x,y] = (*c, 255)
shade = ImageOps.grayscale(screen).point(lambda v: min(255, v * 9))
gradient = ImageChops.multiply(gradient, Image.merge('RGBA', (shade,shade,shade,Image.new('L',(1024,1024),255))))
gradient.putalpha(mask)
base = Image.alpha_composite(base, gradient)
# Extract the original silhouette, including its >_ negative space.
logo = Image.open(ROOT / 'herdr/assets/logo.png').convert('RGB')
alpha = logo.getchannel('R').point(lambda v: max(0,min(255, round((217-v)*255/169))))
alpha = alpha.resize((677,677), Image.Resampling.LANCZOS)
mark = Image.new('L', (1024,1024))
mark.paste(alpha, (173,173))
mark = ImageChops.multiply(mark, mask)
glow = Image.new('RGBA', (1024,1024), (87,157,255,0))
glow.putalpha(mark.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v*.45)))
icon = Image.alpha_composite(base, glow)
goat = Image.new('RGBA', (1024,1024), (215,237,255,0))
goat.putalpha(mark)
icon = Image.alpha_composite(icon, goat)
# Use the actual CRT layer with overlay blending, as in Ghostty.
overlay_rgb = ImageChops.overlay(icon.convert('RGB'), crt.convert('RGB')).convert('RGBA')
overlay_rgb.putalpha(crt.getchannel('A'))
icon = Image.alpha_composite(icon, overlay_rgb)
icon = Image.alpha_composite(icon, gloss)
icon.save(OUT / 'herdr-native.png')
iconset = OUT / 'HerdrNative.iconset'
iconset.mkdir(exist_ok=True)
for size in (16,32,128,256,512):
    for scale in (1,2):
        suffix = '@2x' if scale == 2 else ''
        icon.resize((size*scale,size*scale),Image.Resampling.LANCZOS).save(iconset / f'icon_{size}x{size}{suffix}.png')
# SVG keeps the Herdr geometry editable; terminal shell is embedded raster.
original = (ROOT / 'herdr/assets/logo.svg').read_text()
path = re.search(r'<path d="([^"]+)"',original).group(1)
svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
<title>Herdr Native — Ghostty terminal with Herdr goat</title>
<defs><clipPath id="screen"><rect x="174" y="174" width="675" height="675" rx="107"/></clipPath></defs>
<image width="1024" height="1024" href="{data(base)}"/>
<g clip-path="url(#screen)"><g transform="translate(173 173) scale(1.322265625)"><g fill="#d7edff" transform="translate(0 512) scale(.1 -.1)"><path d="{path}"/></g></g></g>
<image width="1024" height="1024" href="{data(crt)}" style="mix-blend-mode:overlay"/>
<image width="1024" height="1024" href="{data(gloss)}"/>
</svg>'''
(OUT / 'herdr-native.svg').write_text(svg)
# Preview on both light and dark surfaces, with realistic Dock sizes.
preview = Image.new('RGB',(1440,860),'#eceef2')
draw = ImageDraw.Draw(preview)
draw.rectangle((720,0,1440,860), fill='#151820')
for x in (60,780):
    preview.paste(icon.resize((600,600),Image.Resampling.LANCZOS),(x,35),icon.resize((600,600),Image.Resampling.LANCZOS))
    for offset,size in ((100,128),(290,64),(420,32)):
        small=icon.resize((size,size),Image.Resampling.LANCZOS)
        preview.paste(small,(x+offset,690),small)
preview.save(OUT / 'preview.png')
print(OUT)
