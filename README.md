# gate

Small pages and images I link from my AniList profile (Adamas002).

UNOFFICIAL: this is a personal project, not affiliated with AniList.

## What is here

| Path | What it is |
| --- | --- |
| `index.html`, `gate.mp4`, `gate.webm` | The Gate prank page. |
| `farming/`, `demon/` | Two meme pages. |
| `about/` | The animated section titles and the divider for my About section. |
| `worker/visitor-counter.js` | Source of my visitor counter. It runs on Cloudflare, not here. |
| `tools/about/` | The script that draws the About images. |

## Rules this repo follows

- Nothing here calls the AniList API or reads the AniList site, and no AniList data or images are stored here.
- Nothing counts, ranks or rewards likes or follows, and no page names other users.
- No tokens or secrets, and no scheduled robots.

## About images

Drawn with code from an open-licence font (Fredoka Bold, SIL OFL), with no AI image tools and no anime artwork.

```
python3 tools/about/build.py check
```

That rewrites `about/*.gif` and checks them. It needs Pillow, NumPy and SciPy. To change a title, edit `TITLES` in `tools/about/build.py`.

## Visitor counter

`worker/visitor-counter.js` is pasted into a Cloudflare Worker by hand. Its `/badge/adamas002.svg` address is an image that shows the visitor total and counts each visitor once a day. It stores a salted hash of the IP address and the day for two days, never the IP address itself.
