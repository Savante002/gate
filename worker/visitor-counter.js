// Visitor counter for my AniList profiles (Cloudflare Worker, free plan). UNOFFICIAL, not
// affiliated with AniList, and it never calls the AniList API.
//
//   /badge/adamas002.svg -> a small image for the AniList bio that SHOWS the visitor total.
//                           Each load counts one visit, but the same person (same IP) is only
//                           counted once per day.
//   /stats/adamas002     -> {"total": 2184, "today": 37} for that counter.
//   /hit.gif and /stats  -> the old ThePhantasmAl counter (invisible pixel + numbers), unchanged.
//
// Setup (Cloudflare dashboard):
//   1. Workers & Pages > your worker ("phantasm-visitors") > Edit code, replace everything
//      with this file, Deploy. The KV binding COUNTER (namespace "visitors") stays as it is.
//   2. Optional, Settings > Variables: OFFSET_ADAMAS002 = a number to start that counter from,
//      TZ = your time zone for "today" (default America/Toronto).
//   A brand-new worker instead: create it, add a KV namespace, bind it as COUNTER, then step 1.
//
// To add another counter later, add its name (lowercase letters, digits, - or _) to COUNTERS.
//
// Free plan limit: 1,000 KV writes a day. Each NEW visitor of the day uses 3 writes, so about
// 330 new visitors a day are counted (repeat visits cost nothing).
// Privacy: only a salted hash of IP + day is stored, never the IP itself; it expires after 2 days.

const COUNTERS = ["adamas002"];

const GIF = Uint8Array.from(atob("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"), (c) => c.charCodeAt(0));
const BOTS = /bot|crawl|spider|slurp|preview|embed|facebookexternalhit|discord|telegram|curl|wget|python/i;

// Digit outlines from the open-licence font Fredoka Bold (SIL OFL), in font units (1000 per em,
// y pointing up), so the badge looks the same on every device without loading a font.
const GLYPHS = {
  "0": [565, "M286 -22Q216 -22 168 2Q119 27 89 67Q59 107 42 155Q26 203 20 252Q14 300 14 340Q14 376 20 423Q26 470 42 518Q57 567 87 608Q117 650 166 676Q214 701 284 701Q352 701 400 676Q447 651 476 610Q506 570 522 522Q539 473 545 425Q551 377 551 339Q551 299 544 250Q538 202 522 154Q505 106 476 66Q446 26 400 2Q353 -22 286 -22ZM282 162Q308 162 324 174Q340 187 349 207Q358 227 362 250Q366 274 367 296Q368 319 368 335V340Q368 398 358 438Q349 477 330 498Q312 518 283 518Q253 518 234 498Q215 478 206 438Q197 398 197 339Q197 282 206 242Q216 203 235 182Q254 162 282 162Z"],
  "1": [379, "M259 -11Q209 -11 189 9Q169 29 169 80V418L159 409Q125 379 98 380Q70 381 38 415Q5 450 7 481Q9 512 49 547L176 661Q184 669 193 674Q200 680 211 684Q219 687 227 688Q241 691 260 691Q310 691 330 670Q350 650 350 599V79Q350 29 330 9Q310 -11 259 -11Z"],
  "2": [566, "M105 -11Q68 -11 41 17Q14 45 14 82Q14 101 18 118Q23 135 31 150Q39 166 50 179Q76 212 114 239Q152 266 193 290Q234 314 270 338Q306 362 328 390Q351 418 351 452Q351 462 346 478Q342 493 326 506Q310 518 275 518Q254 518 239 512Q224 507 215 496Q206 486 202 470Q197 455 197 435Q197 414 193 394Q189 375 170 362Q151 350 105 350Q60 350 41 364Q22 377 18 398Q14 420 14 442Q14 488 32 534Q49 580 83 618Q117 655 165 678Q213 701 274 701Q347 701 406 666Q465 630 500 570Q535 511 535 437Q535 388 519 348Q503 308 478 276Q452 245 423 222Q394 200 367 186Q354 178 343 173H468Q491 173 512 169Q533 165 546 146Q559 126 559 80Q559 36 546 16Q532 -3 510 -7Q489 -11 466 -11Z"],
  "3": [565, "M281 -21Q227 -21 179 -3Q131 15 94 46Q56 78 35 118Q14 159 14 203Q14 266 114 266Q142 266 156 261Q171 256 180 244Q189 231 198 208L205 196Q211 183 229 170Q247 158 282 158Q309 158 328 165Q347 172 358 185Q368 198 368 215Q368 235 358 248Q348 261 331 267Q314 273 291 273Q269 273 248 276Q227 279 214 297Q200 315 200 360Q200 406 214 424Q227 441 248 444Q270 446 292 446Q312 446 326 448Q339 451 346 458Q353 465 353 478Q353 493 346 504Q338 514 324 520Q310 525 291 525Q267 525 253 519Q239 513 232 504Q224 494 219 485Q200 443 168 434Q137 426 93 450Q50 476 44 505Q38 534 58 574Q58 574 58 574Q58 574 64 586Q69 598 84 617Q99 636 124 656Q149 675 188 688Q227 701 281 701Q344 701 392 681Q439 661 472 629Q504 597 520 558Q537 520 537 482Q537 445 524 420Q512 394 496 380Q479 365 468 359Q477 353 491 342Q505 330 518 312Q532 294 542 270Q551 247 551 217Q551 148 516 94Q481 41 420 10Q359 -21 281 -21Z"],
  "4": [551, "M430 -8Q385 -8 366 6Q347 19 343 40Q339 62 339 84V188H96Q51 188 32 202Q13 215 9 236Q5 258 5 280Q5 291 6 298Q6 305 8 312L85 623Q98 678 123 689Q148 700 198 688Q235 678 256 660Q276 641 270 615L208 360H339V605Q339 627 343 646Q347 666 366 679Q386 692 432 692Q477 692 496 678Q515 665 519 644Q523 623 523 600V83Q523 60 518 39Q514 18 495 5Q476 -8 430 -8Z"],
  "5": [493, "M236 -22Q189 -22 153 -12Q117 -1 92 13Q68 27 56 38L43 48Q28 61 16 76Q5 90 5 109Q5 125 14 142Q23 158 42 177Q57 194 72 202Q88 211 104 211Q119 211 134 204Q150 196 172 178L178 174Q183 170 196 166Q208 162 230 162Q251 162 266 169Q280 176 288 188Q295 201 295 219Q295 236 287 249Q279 262 265 270Q251 277 233 277Q221 277 212 274Q204 271 200 268Q195 264 194 263Q184 254 171 246Q158 239 138 239Q123 239 107 244Q91 249 73 259Q41 276 28 289Q16 302 16 318L53 602Q55 617 61 638Q67 660 86 676Q105 693 145 693L374 692Q397 692 418 688Q438 684 452 664Q465 645 465 598Q465 555 452 536Q440 518 420 514Q400 511 379 511H225L218 458Q221 459 224 459Q227 459 230 460Q232 460 235 460H240Q287 460 330 442Q373 423 406 390Q440 357 460 315Q479 273 479 226Q479 156 447 100Q415 44 360 11Q305 -22 236 -22Z"],
  "6": [521, "M267 -21Q188 -21 132 14Q75 48 44 112Q14 177 14 266Q14 342 32 407Q50 472 84 524Q117 577 164 614Q212 651 272 671Q331 691 400 691Q422 691 442 687Q462 683 475 664Q488 645 488 599Q488 554 474 535Q461 516 440 512Q419 508 396 508Q351 508 320 496Q288 484 268 466Q247 448 235 430Q226 417 221 407Q229 410 240 412Q256 416 274 416Q337 416 390 387Q443 358 475 311Q507 264 507 208Q507 144 475 92Q443 40 388 10Q334 -21 267 -21ZM263 150Q285 150 299 156Q313 161 320 173Q326 185 326 203Q326 218 319 229Q312 240 299 246Q286 252 269 252Q249 252 234 246Q218 240 210 230Q202 219 202 205Q202 186 208 174Q214 161 228 156Q241 150 263 150Z"],
  "7": [522, "M193 -9Q148 -9 129 1Q110 11 106 30Q101 48 101 71Q101 123 114 171Q126 219 146 263Q167 307 190 346Q214 384 236 416Q258 447 274 470Q291 493 296 507H96Q74 507 53 512Q32 516 18 535Q5 554 5 599Q5 644 18 663Q31 682 50 686Q70 690 91 690H426Q472 690 490 676Q509 663 513 642Q517 621 517 598Q517 559 500 516Q483 472 457 426Q431 381 401 334Q371 288 345 242Q319 196 302 152Q285 109 285 69Q285 47 280 29Q276 11 256 1Q237 -9 193 -9Z"],
  "8": [541, "M273 -22Q200 -22 142 10Q83 41 48 96Q14 150 14 219Q14 252 25 280Q36 307 51 327Q66 347 79 359Q83 362 86 365Q89 363 91 361Q89 363 86 366Q92 371 94 373Q95 373 95 373Q94 373 85 367Q80 372 74 379Q59 395 46 422Q34 449 34 485Q34 547 66 596Q98 646 152 676Q207 705 273 705Q338 705 391 676Q444 646 476 595Q507 544 507 481Q507 457 499 436Q491 414 480 398Q469 381 460 371Q458 369 457 367Q448 373 448 373Q448 373 449 373Q451 371 456 366Q451 360 450 359Q449 359 449 359Q450 358 457 365Q460 362 465 358Q477 346 492 326Q506 306 516 278Q527 251 527 219Q527 162 504 117Q481 72 444 41Q406 10 362 -6Q317 -22 273 -22ZM272 153Q295 153 311 160Q327 168 336 182Q344 196 344 215Q344 234 336 248Q327 261 310 268Q294 275 270 275Q247 275 230 268Q214 261 206 248Q197 234 197 215Q197 196 206 182Q215 168 232 160Q248 153 272 153ZM272 444Q289 444 300 449Q312 454 318 464Q324 473 324 486Q324 499 318 508Q311 518 300 523Q288 528 271 528Q255 528 243 523Q231 518 225 508Q219 499 219 486Q219 473 226 464Q232 454 244 449Q256 444 272 444ZM457 367Q457 367 458 366Q458 366 457 365Q456 366 456 366Q456 367 457 367ZM86 365Q85 365 84 366Q85 366 85 367Q86 366 86 366Q86 365 86 365Z"],
  "9": [521, "M117 -10Q96 -10 76 -6Q57 -1 44 18Q32 37 32 82Q32 128 46 146Q59 165 80 169Q102 173 125 173Q170 173 202 185Q233 197 254 215Q274 233 286 251Q294 265 299 275Q291 272 281 270Q264 265 246 265Q184 265 131 294Q78 323 46 370Q14 417 14 472Q14 537 46 589Q78 641 132 672Q186 702 253 702Q332 702 389 668Q446 633 476 569Q507 505 507 415Q507 339 489 274Q471 208 438 156Q404 103 356 66Q308 29 248 10Q188 -10 117 -10ZM252 429Q273 429 288 434Q303 440 311 451Q319 462 319 476Q319 496 312 508Q306 520 292 526Q279 531 257 531Q235 531 221 526Q207 520 200 508Q194 497 194 478Q194 464 202 452Q209 441 222 435Q235 429 252 429Z"],
  ",": [223, "M42 -157 36 -155Q29 -153 22 -144Q16 -135 16 -113Q16 -87 24 -78Q33 -69 38 -68Q40 -66 56 -52Q72 -39 76 -8Q63 -5 52 0Q42 6 34 13Q20 27 18 48Q15 69 15 88Q16 110 20 132Q24 153 44 168Q65 182 113 182Q167 182 189 160Q203 146 206 126Q208 105 208 85Q208 75 208 66Q207 56 205 47Q200 -10 176 -56Q153 -102 118 -130Q83 -157 42 -157Z"],
  " ": [235, ""],
  "T": [640, "M549 692Q568 692 585 689Q602 686 614 670Q625 654 625 615Q625 577 614 560Q602 544 584 541Q567 538 548 538H410V80Q410 58 406 38Q402 17 384 4Q365 -9 319 -9Q275 -9 256 4Q237 18 234 38Q230 59 230 81V538H91Q73 538 56 542Q38 545 26 561Q15 577 15 616Q15 654 26 670Q38 686 56 689Q74 692 92 692Z"],
  "O": [721, "M365 -20Q292 -20 228 8Q165 35 117 84Q69 133 42 198Q14 262 14 335Q14 411 40 477Q67 543 114 593Q161 643 224 671Q286 699 357 699Q430 699 494 672Q557 644 605 595Q653 546 680 480Q707 415 707 339Q707 265 680 200Q654 135 606 86Q559 36 498 8Q436 -20 365 -20ZM360 163Q394 163 424 176Q454 190 476 214Q499 237 512 270Q524 302 524 339Q524 376 511 408Q498 440 476 464Q453 489 424 502Q394 516 361 516Q327 516 298 502Q268 489 246 464Q223 440 210 408Q197 376 197 339Q197 303 210 271Q223 239 246 214Q268 190 298 176Q327 163 360 163Z"],
  "D": [655, "M119 -8Q76 -8 57 5Q38 18 33 39Q28 60 28 84V601Q28 623 32 644Q36 665 55 678Q74 692 120 691H292Q363 691 426 664Q489 637 538 590Q586 542 614 479Q641 416 641 345Q641 272 614 208Q586 144 538 96Q489 47 426 20Q362 -8 289 -8ZM211 175H292Q337 175 375 198Q413 220 436 258Q458 295 458 341Q458 387 436 425Q413 463 375 486Q337 508 291 508H211Z"],
  "A": [711, "M690 118Q705 88 706 67Q707 46 693 30Q679 14 648 -2Q600 -25 574 -16Q548 -8 525 41L479 136H233L187 40Q163 -7 137 -16Q111 -24 64 -1Q33 14 18 30Q4 46 5 67Q6 88 21 118L272 638Q284 662 308 676Q331 691 356 691Q374 691 390 684Q405 678 418 666Q431 654 439 637ZM402 292 355 389 308 292Z"],
  "Y": [638, "M584 676Q628 646 632 618Q636 591 610 553L411 255V80Q411 58 407 37Q403 16 384 2Q365 -11 319 -11Q273 -11 254 2Q235 16 231 38Q227 59 227 82V255L28 553Q0 592 6 620Q12 648 55 676Q86 697 108 700Q130 704 148 692Q166 679 183 651L316 437L455 651Q474 680 492 692Q509 704 531 700Q553 696 584 676Z"],
};

function today(env) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: env.TZ || "America/Toronto" }).format(new Date());
}

async function sha(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].slice(0, 12).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function num(env, key) {
  return parseInt((await env.COUNTER.get(key)) || "0", 10) || 0;
}

// Count one visit for the counter whose keys start with `prefix` ("" = the old counter).
async function countVisit(request, env, prefix) {
  const ua = request.headers.get("User-Agent") || "";
  if (BOTS.test(ua)) return;
  const day = today(env);
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const seen = prefix + "seen:" + (await sha(ip + "|" + day + "|" + (env.SALT || "phantasm")));
  if (await env.COUNTER.get(seen)) return;
  await env.COUNTER.put(seen, "1", { expirationTtl: 2 * 86400 });
  await env.COUNTER.put(prefix + "total", String((await num(env, prefix + "total")) + 1));
  await env.COUNTER.put(prefix + "day:" + day, String((await num(env, prefix + "day:" + day)) + 1),
                        { expirationTtl: 3 * 86400 });
}

async function readStats(env, prefix, offset) {
  return {
    total: (await num(env, prefix + "total")) + offset,
    today: await num(env, prefix + "day:" + today(env)),
  };
}

// One line of text as SVG paths, centred on x = cx with its baseline at y.
function textPaths(text, cx, y, size, spacing, maxWidth) {
  let s = size / 1000;
  const chars = [...text].filter((c) => GLYPHS[c]);
  const width = (k) => chars.reduce((w, c) => w + GLYPHS[c][0] * k, 0) + spacing * Math.max(0, chars.length - 1);
  if (width(s) > maxWidth) s *= maxWidth / width(s);
  let x = cx - width(s) / 2;
  let out = "";
  for (const c of chars) {
    if (GLYPHS[c][1]) {
      out += `<path transform="translate(${x.toFixed(1)} ${y}) scale(${s.toFixed(4)} ${(-s).toFixed(4)})" d="${GLYPHS[c][1]}"/>`;
    }
    x += GLYPHS[c][0] * s + spacing;
  }
  return out;
}

// The badge: a dark plate (the AniList dark-theme card colour) with the total in slime-blue neon.
function badge(stats) {
  const total = stats.total.toLocaleString("en-US");
  const todayLine = stats.today.toLocaleString("en-US") + " TODAY";
  return `<svg xmlns="http://www.w3.org/2000/svg" width="440" height="120" viewBox="0 0 440 120" role="img" aria-label="${total} visitors">
<title>${total} visitors</title>
<defs>
<linearGradient id="tube" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#F7FCFC"/><stop offset="0.55" stop-color="#CCE9F6"/><stop offset="1" stop-color="#93B9E8"/></linearGradient>
<filter id="far" x="-20%" y="-60%" width="140%" height="220%"><feGaussianBlur stdDeviation="9"/></filter>
<filter id="near" x="-10%" y="-30%" width="120%" height="160%"><feGaussianBlur stdDeviation="2.6"/></filter>
<g id="n">${textPaths(total, 220, 68, 72, 3, 380)}</g>
<g id="t">${textPaths(todayLine, 220, 101, 22, 2.2, 380)}</g>
</defs>
<rect width="440" height="120" rx="20" fill="#151F2E"/>
<use href="#n" fill="#3A71A4" filter="url(#far)" opacity="0.95"><animate attributeName="opacity" values="0.95;0.6;0.95" dur="2.8s" repeatCount="indefinite"/></use>
<use href="#n" fill="#5E97D0" filter="url(#near)" opacity="0.9"/>
<use href="#n" fill="url(#tube)"/>
<use href="#t" fill="#3A71A4" filter="url(#near)" opacity="0.8"/>
<use href="#t" fill="#93B9E8"/>
</svg>`;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const cors = { "Access-Control-Allow-Origin": "*" };
    const fresh = { "Cache-Control": "no-store, max-age=0" };

    // New: a named counter that draws its own number.
    let m = url.pathname.match(/^\/(badge|stats)\/([a-z0-9_-]{1,32})(\.svg)?$/);
    if (m) {
      const name = m[2];
      if (!COUNTERS.includes(name)) return new Response("Unknown counter", { status: 404, headers: cors });
      const prefix = "c:" + name + ":";
      const offset = parseInt(env["OFFSET_" + name.toUpperCase().replace(/-/g, "_")] || "0", 10) || 0;
      if (m[1] === "badge") {
        await countVisit(request, env, prefix);
        return new Response(badge(await readStats(env, prefix, offset)), {
          headers: { "Content-Type": "image/svg+xml; charset=utf-8", ...fresh, ...cors },
        });
      }
      return new Response(JSON.stringify(await readStats(env, prefix, offset)), {
        headers: { "Content-Type": "application/json", ...fresh, ...cors },
      });
    }

    // Old ThePhantasmAl counter, unchanged.
    if (url.pathname === "/hit.gif" || url.pathname === "/hit") {
      await countVisit(request, env, "");
      return new Response(GIF, { headers: { "Content-Type": "image/gif", ...fresh, ...cors } });
    }

    if (url.pathname === "/stats") {
      const offset = parseInt(env.OFFSET || "0", 10) || 0;
      return new Response(JSON.stringify(await readStats(env, "", offset)), {
        headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...cors },
      });
    }

    return new Response("Visitor counter", { headers: cors });
  },
};
