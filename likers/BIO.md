# Hall of Likes — AniList bio code

Replace your flag counter block with this. Change `YOUR-WORKER` to your Cloudflare Worker
address (for example `phantasm-visitors.yourname.workers.dev`).

```
~~~img1(https://YOUR-WORKER/hit.gif)~~~

~~~[img700(https://savante002.github.io/gate/likers/img/header.png)](https://savante002.github.io/gate/likers/)~~~

~~~[img700(https://savante002.github.io/gate/likers/img/alltime.png)](https://savante002.github.io/gate/likers/#alltime)~~~

~~~[img228(https://savante002.github.io/gate/likers/img/month.png)](https://savante002.github.io/gate/likers/#month) [img228(https://savante002.github.io/gate/likers/img/week.png)](https://savante002.github.io/gate/likers/#week) [img228(https://savante002.github.io/gate/likers/img/day.png)](https://savante002.github.io/gate/likers/#day)~~~

~~~[img700(https://savante002.github.io/gate/likers/img/legend.png)](https://savante002.github.io/gate/likers/)~~~
```

- `img1(...)` is the invisible counting pixel: every profile view loads it once.
- The images are redrawn every 15 minutes. GitHub Pages lets browsers keep a copy for
  up to 10 minutes, so a change can take up to ~25 minutes to show.
- Each image links to its own tab of the full list page.
