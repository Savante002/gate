# Hall of Likes — AniList bio code

Paste this in AniList → Settings → About, in place of the old flag counter line.
It uses HTML links (like the flag counter did): AniList does not turn
`[img700(...)](link)` into a clickable image.

```
~~~<img src="https://phantasm-visitors.kmy876zcnr.workers.dev/hit.gif" width="1" height="1">~~~

~~~<a href="https://savante002.github.io/gate/likers/"><img src="https://savante002.github.io/gate/likers/img/header.png" width="700"></a>~~~

~~~<a href="https://savante002.github.io/gate/likers/#alltime"><img src="https://savante002.github.io/gate/likers/img/alltime.png" width="700"></a>~~~

~~~<a href="https://savante002.github.io/gate/likers/#month"><img src="https://savante002.github.io/gate/likers/img/month.png" width="228"></a> <a href="https://savante002.github.io/gate/likers/#week"><img src="https://savante002.github.io/gate/likers/img/week.png" width="228"></a> <a href="https://savante002.github.io/gate/likers/#day"><img src="https://savante002.github.io/gate/likers/img/day.png" width="228"></a>~~~

~~~<a href="https://savante002.github.io/gate/likers/"><img src="https://savante002.github.io/gate/likers/img/legend.png" width="700"></a>~~~
```

- The first line is the invisible counting pixel: every profile view loads it once.
- The images are redrawn every 15 minutes. GitHub Pages lets browsers keep a copy for
  up to 10 minutes, so a change can take up to ~25 minutes to show.
- Each image links to its own tab of the full list page.
