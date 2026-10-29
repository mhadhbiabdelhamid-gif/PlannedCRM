/* Planned Real Estate — "Gold Light" post designs, drawn on <canvas>.
 *
 * One file holds every post type so they stay one family: the same header
 * (logo left, series label right), the same gold roof line above headlines,
 * the same dark footer. Change a token here and every post follows.
 *
 *   await PostCards.ready(baseUrl)            // fonts + logo, once per page
 *   const c = await PostCards.draw(card)      // -> 1080x1350 <canvas> (feed post)
 *   await PostCards.draw(card, null, "story")    // -> 1080x1920 story / video end card
 *   await PostCards.draw(card, null, "overlay")  // -> 1080x1920 transparent frame laid
 *                                                //    over a reel or video story
 *
 * Stories and reels keep everything that matters between y=250 and y=1670:
 * Instagram draws its progress bar, name and reply box over the rest. The
 * overlay also keeps clear of the right-hand 150px where reels show the
 * like / comment / share buttons.
 *
 * card.type: "rent" | "sale" | "rented" | "sold" | "tip" | "area" | "market" | "news"
 * Drawn in the browser so the picture you approve is exactly what is posted.
 */
(function () {
  "use strict";
  const FORMATS = {
    post:    { W: 1080, H: 1350, head: 56,  foot: 1232 },
    story:   { W: 1080, H: 1920, head: 250, foot: 1560 },
    overlay: { W: 1080, H: 1920, head: 210, foot: 1920 },
  };
  let F = FORMATS.post, W = F.W, H = F.H;
  const T = {
    bg: "#FAF8F4", ink: "#14120F", sub: "#57524A", line: "#E4DDD0",
    goldL: "#E8C468", gold: "#C9A227", goldD: "#9C6F1E", goldTxt: "#8A5F14", dark: "#14120F",
  };
  const CONTACT = { phone: "+974 3111 4024", web: "PLANNEDREALESTATE.COM",
                    tagAr: "شريكك العقاري الموثوق", tagEn: "YOUR TRUSTED REAL ESTATE PARTNER" };

  // Line icons on a 24-unit grid (circles written as arcs so Path2D takes them).
  const ICON = {
    bed: "M3 18v-6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v6M3 15h18M3 18v2M21 18v2M6 10V7.5A1.5 1.5 0 0 1 7.5 6h3A1.5 1.5 0 0 1 12 7.5V10M12 10V7.5A1.5 1.5 0 0 1 13.5 6h3A1.5 1.5 0 0 1 18 7.5V10",
    bath: "M3 12h18v2a5 5 0 0 1-5 5H8a5 5 0 0 1-5-5v-2zM6 12V5.5a2.5 2.5 0 0 1 5 0M7 19l-1 2M17 19l1 2",
    sofa: "M5 10V8a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v2M3 17v-5a2 2 0 0 1 4 0v2h10v-2a2 2 0 0 1 4 0v5zM5 17v2M19 17v2",
    size: "M4 9V4h5M20 15v5h-5M4 4l6 6M20 20l-6-6M15 4h5v5M9 20H4v-5",
    building: "M4 21V5l8-3v19M12 21V9l8 3v9M2 21h20M7 8h2M7 12h2M7 16h2M15 14h2M15 18h2",
    key: "M14 10a4 4 0 1 1-8 0 4 4 0 0 1 8 0zM13 12l8 8M18 17l2-2M16 15l2-2",
    phone: "M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2",
    globe: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18",
    pin: "M12 21s-7-7.5-7-12a7 7 0 0 1 14 0c0 4.5-7 12-7 12zM14.5 9a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z",
    chat: "M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z",
    news: "M4 5h13v14H6a2 2 0 0 1-2-2V5zM17 9h3v8a2 2 0 0 1-2 2h-1M7 9h7M7 12.5h7M7 16h4",
    shield: "M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6l8-3zM8.5 12l2.5 2.5 4.5-5",
  };

  let LOGO = null;
  const imgCache = {};

  function loadImage(src) {
    if (!src) return Promise.resolve(null);
    if (imgCache[src]) return imgCache[src];
    imgCache[src] = new Promise((res) => {
      const im = new Image();
      im.crossOrigin = "anonymous";
      im.onload = () => res(im);
      im.onerror = () => res(null);
      im.src = src;
    });
    return imgCache[src];
  }

  async function ready(base) {
    base = (base || "/static").replace(/\/$/, "");
    const faces = [
      ["Montserrat", "Montserrat-600.woff2", 600], ["Montserrat", "Montserrat-700.woff2", 700],
      ["Montserrat", "Montserrat-800.woff2", 800], ["Montserrat", "Montserrat-900.woff2", 900],
      ["Cairo", "Cairo-Arabic-600.woff2", 600], ["Cairo", "Cairo-Arabic-700.woff2", 700],
      ["Cairo", "Cairo-Arabic-900.woff2", 900],
    ];
    await Promise.all(faces.map(async ([fam, file, w]) => {
      try {
        const f = new FontFace(fam, `url(${base}/fonts/brand/${file})`, { weight: String(w) });
        document.fonts.add(await f.load());
      } catch (e) { /* falls back to system fonts; the post still draws */ }
    }));
    LOGO = await loadImage(`${base}/img/brand/logo-hd.png`);
  }

  // ------------------------------------------------------------- helpers
  function font(ctx, weight, size, fam) { ctx.font = `${weight} ${size}px ${fam}, sans-serif`; }
  function spacing(ctx, px) { if ("letterSpacing" in ctx) ctx.letterSpacing = px + "px"; }
  function goldGrad(ctx, x0, x1) {
    const g = ctx.createLinearGradient(x0, 0, x1, 0);
    g.addColorStop(0, T.goldD); g.addColorStop(0.45, T.gold); g.addColorStop(0.75, T.goldL); g.addColorStop(1, T.gold);
    return g;
  }
  function textW(ctx, s) { return ctx.measureText(s).width; }

  function icon(ctx, name, x, y, size, color, lw) {
    ctx.save();
    ctx.translate(x, y); ctx.scale(size / 24, size / 24);
    ctx.strokeStyle = color; ctx.lineWidth = lw || 1.6;
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.stroke(new Path2D(ICON[name]));
    ctx.restore();
  }

  // The roof line from the logo: the signature shape over every headline.
  function roof(ctx, x, y, w, h, stroke) {
    ctx.save();
    ctx.strokeStyle = goldGrad(ctx, x, x + w);
    ctx.lineWidth = stroke; ctx.lineCap = "square"; ctx.lineJoin = "miter";
    ctx.beginPath();
    ctx.moveTo(x + stroke, y + h - stroke); ctx.lineTo(x + w / 2, y + stroke); ctx.lineTo(x + w - stroke, y + h - stroke);
    ctx.stroke(); ctx.restore();
  }

  function cover(ctx, img, x, y, w, h, posX) {
    if (!img) { ctx.fillStyle = "#E9E4DA"; ctx.fillRect(x, y, w, h); return; }
    const s = Math.max(w / img.width, h / img.height);
    const sw = w / s, sh = h / s;
    const px = posX == null ? 0.5 : posX;
    ctx.drawImage(img, (img.width - sw) * px, (img.height - sh) / 2, sw, sh, x, y, w, h);
  }

  // Break Arabic (or English) text into lines no wider than maxW.
  function wrap(ctx, text, maxW) {
    const words = String(text || "").split(/\s+/).filter(Boolean);
    const lines = []; let line = "";
    for (const w of words) {
      const t = line ? line + " " + w : w;
      if (line && textW(ctx, t) > maxW) { lines.push(line); line = w; } else line = t;
    }
    if (line) lines.push(line);
    return lines;
  }

  // Largest size (from max down to min) at which text fits in maxLines lines.
  function fitLines(ctx, text, weight, max, min, fam, maxW, maxLines) {
    let size = max, lines;
    for (; size >= min; size -= 2) {
      font(ctx, weight, size, fam); lines = wrap(ctx, text, maxW);
      if (lines.length <= maxLines) return { size, lines };
    }
    font(ctx, weight, min, fam);
    return { size: min, lines: wrap(ctx, text, maxW) };
  }

  function fmtPrice(n) {
    const v = Number(n);
    return isFinite(v) && v > 0 ? Math.round(v).toLocaleString("en-US") : "";
  }

  // ------------------------------------------------------- shared frame
  function header(ctx, tagAr, tagEn) {
    if (LOGO) {
      const h = 138, w = h * LOGO.width / LOGO.height;
      ctx.drawImage(LOGO, 60, F.head, w, h);
    }
    ctx.save();
    ctx.direction = "rtl"; ctx.textAlign = "right"; ctx.textBaseline = "alphabetic";
    const o = F.head - 56;
    font(ctx, 700, 40, "Cairo"); ctx.fillStyle = T.ink; ctx.fillText(tagAr, W - 60, 106 + o);
    const wa = textW(ctx, tagAr);
    ctx.direction = "ltr"; font(ctx, 700, 20, "Montserrat"); spacing(ctx, 4.8);
    ctx.fillStyle = T.goldTxt; ctx.fillText(tagEn, W - 60 + 4.8, 138 + o);
    const we = textW(ctx, tagEn);
    spacing(ctx, 0);
    const right = W - 60 - Math.max(wa, we) - 18;
    ctx.fillStyle = goldGrad(ctx, right - 120, right); ctx.fillRect(right - 120, 105 + o, 120, 2);
    ctx.restore();
  }

  function footer(ctx) {
    const y = F.foot, cy = y + 59;
    ctx.save();
    ctx.fillStyle = T.dark; ctx.fillRect(0, y, W, H - y);
    if (H - y > 118) {        // story: a gold hairline closes the contact row
      ctx.fillStyle = goldGrad(ctx, 52, W - 52); ctx.fillRect(52, y + 118, W - 104, 1);
    }
    ctx.strokeStyle = T.gold; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(52 + 25, cy, 25, 0, Math.PI * 2); ctx.stroke();
    icon(ctx, "phone", 52 + 10, cy - 15, 30, T.goldL);
    font(ctx, 600, 22, "Montserrat"); ctx.fillStyle = "#fff"; ctx.textBaseline = "middle";
    ctx.fillText(CONTACT.phone, 52 + 50 + 14, cy);
    let x = 52 + 50 + 14 + textW(ctx, CONTACT.phone) + 26;
    ctx.fillStyle = "#4a453c"; ctx.fillRect(x, cy - 23, 1, 46); x += 27;
    icon(ctx, "globe", x, cy - 15, 30, T.goldL); x += 44;
    font(ctx, 600, 18, "Montserrat"); spacing(ctx, 2.5); ctx.fillStyle = "#fff"; ctx.fillText(CONTACT.web, x, cy);
    spacing(ctx, 0);
    ctx.textAlign = "right"; ctx.direction = "rtl"; font(ctx, 700, 23, "Cairo"); ctx.fillStyle = T.goldL;
    ctx.fillText(CONTACT.tagAr, W - 52, cy - 10);
    ctx.direction = "ltr"; font(ctx, 600, 11, "Montserrat"); spacing(ctx, 1.5); ctx.fillStyle = "#b9ae96";
    ctx.fillText(CONTACT.tagEn, W - 52 + 1.5, cy + 16);
    ctx.restore();
  }

  function accent(ctx, x0, y0, w, h, slope) {
    // Soft gold parallelogram (the skewed band behind photos and figures).
    const t = Math.tan(slope * Math.PI / 180), cx = x0 + w / 2;
    ctx.save(); ctx.globalAlpha = 0.2;
    ctx.fillStyle = goldGrad(ctx, x0, x0 + w);
    ctx.beginPath();
    ctx.moveTo(x0, y0 + (x0 - cx) * t); ctx.lineTo(x0 + w, y0 + (x0 + w - cx) * t);
    ctx.lineTo(x0 + w, y0 + h + (x0 + w - cx) * t); ctx.lineTo(x0, y0 + h + (x0 - cx) * t);
    ctx.closePath(); ctx.fill(); ctx.restore();
  }

  function framedPhoto(ctx, img, x, y, w, h, deg) {
    ctx.save();
    ctx.translate(x + w / 2, y + h / 2); ctx.rotate(deg * Math.PI / 180);
    ctx.shadowColor = "rgba(20,18,15,.22)"; ctx.shadowBlur = 30; ctx.shadowOffsetY = 14;
    ctx.fillStyle = "#fff"; ctx.fillRect(-w / 2, -h / 2, w, h);
    ctx.shadowColor = "transparent";
    cover(ctx, img, -w / 2 + 6, -h / 2 + 6, w - 12, h - 12);
    ctx.restore();
  }

  function whiteCard(ctx, x, y, w, h) {
    ctx.save();
    ctx.shadowColor = "rgba(20,18,15,.07)"; ctx.shadowBlur = 24; ctx.shadowOffsetY = 8;
    ctx.fillStyle = "#fff"; ctx.fillRect(x, y, w, h);
    ctx.shadowColor = "transparent"; ctx.strokeStyle = T.line; ctx.lineWidth = 1;
    ctx.strokeRect(x + .5, y + .5, w - 1, h - 1);
    ctx.restore();
  }

  // ------------------------------------------------------------ listing
  function listing(ctx, c, photos) {
    ctx.fillStyle = T.bg; ctx.fillRect(0, 0, W, H);
    if (photos[0]) {
      ctx.save(); ctx.filter = "brightness(1.1) contrast(1.04)";
      cover(ctx, photos[0], 0, 0, W, H, c.photoPos == null ? 0.3 : c.photoPos);
      ctx.restore();
    }
    // Light wash on the left so the text sits on calm, readable ground.
    let g = ctx.createLinearGradient(0, 0, W, 0);
    g.addColorStop(0, T.bg); g.addColorStop(.36, "rgba(250,248,244,.93)");
    g.addColorStop(.56, "rgba(250,248,244,.45)"); g.addColorStop(.72, "rgba(250,248,244,0)");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    g = ctx.createLinearGradient(0, H - 380, 0, H);
    g.addColorStop(0, "rgba(250,248,244,0)"); g.addColorStop(.5, T.bg); g.addColorStop(1, T.bg);
    ctx.fillStyle = g; ctx.fillRect(0, H - 380, W, 380);
    g = ctx.createLinearGradient(0, 0, 0, 260);
    g.addColorStop(0, "rgba(250,248,244,.85)"); g.addColorStop(1, "rgba(250,248,244,0)");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, 260);

    if (photos[1] || photos[2]) accent(ctx, 520, 720, 620, 300, 12);
    if (photos[1]) framedPhoto(ctx, photos[1], 688, 715, 340, 240, -3);
    if (photos[2]) framedPhoto(ctx, photos[2], 530, 815, 260, 190, 2.5);

    const sold = c.type === "rented" || c.type === "sold";
    const tag = { rent: ["للإيجار", "FOR RENT"], sale: ["للبيع", "FOR SALE"],
                  rented: ["تم التأجير", "RENTED"], sold: ["تم البيع", "SOLD"] }[c.type] || ["للإيجار", "FOR RENT"];
    header(ctx, tag[0], tag[1]);

    // --- text block, right-aligned inside a column that starts at x=66
    ctx.save();
    ctx.direction = "rtl"; ctx.textAlign = "right"; ctx.textBaseline = "alphabetic";
    const fit = fitLines(ctx, c.titleAr, 900, 74, 50, "Cairo", 600, 2);
    const titleLines = fit.lines, ts = fit.size;
    // English line: shrink (25px down to 16px) until it fits the column.
    let es = 25, enW = 0;
    for (; es >= 16; es--) {
      font(ctx, 600, es, "Montserrat"); spacing(ctx, es * 0.26);
      enW = textW(ctx, c.titleEn || ""); if (enW <= 600) break;
    }
    spacing(ctx, 0);
    font(ctx, 900, ts, "Cairo");
    const blockW = Math.min(600, Math.max(enW, ...titleLines.map((l) => textW(ctx, l)), 300));
    const R = 66 + blockW;
    let y = 250;
    roof(ctx, R - 190, y, 190, 58, 7); y += 64;
    ctx.fillStyle = T.ink;
    for (const l of titleLines) { y += ts; ctx.fillText(l, R, y); y += Math.round(ts * .18); }
    if (c.titleEn) {
      ctx.direction = "ltr"; font(ctx, 600, es, "Montserrat"); spacing(ctx, es * 0.26); ctx.fillStyle = T.sub;
      y += 30; ctx.fillText(c.titleEn, R + es * 0.26, y); spacing(ctx, 0); ctx.direction = "rtl";
    }
    y += 26;
    ctx.fillStyle = goldGrad(ctx, R - 120, R); ctx.fillRect(R - 120, y, 120, 3); y += 3;

    if (c.areaAr || c.areaEn) {
      y += 20;
      icon(ctx, "pin", R - 44, y + 4, 44, T.goldD, 1.8);
      font(ctx, 700, 34, "Cairo"); ctx.fillStyle = T.ink;
      ctx.fillText(c.areaAr || c.areaEn, R - 56, y + 34);
      if (c.areaAr && c.areaEn) {
        ctx.direction = "ltr"; font(ctx, 600, 22, "Montserrat"); ctx.fillStyle = T.sub;
        ctx.fillText(c.areaEn, R - 56, y + 64); ctx.direction = "rtl";
      }
      y += 70;
    }

    const price = fmtPrice(c.price);
    if (price) {
      y += 30;
      const big = price.length > 7 ? 60 : 80;
      font(ctx, 900, big, "Montserrat"); const vw = textW(ctx, price);
      font(ctx, 700, 21, "Montserrat"); spacing(ctx, 2.5);
      const unit2 = c.type === "sale" || c.type === "sold" ? "TOTAL" : "/ MONTH";
      const uw = Math.max(textW(ctx, "QAR"), textW(ctx, unit2)); spacing(ctx, 0);
      const bw = 30 + vw + 20 + 2 + 20 + uw + 30, bh = big + 32 + 16;
      const bx = R - bw, by = y;
      ctx.save();
      ctx.shadowColor = "rgba(156,111,30,.25)"; ctx.shadowBlur = 24; ctx.shadowOffsetY = 10;
      const pg = ctx.createLinearGradient(bx, 0, bx + bw, 0);
      pg.addColorStop(0, T.goldD); pg.addColorStop(.45, T.gold); pg.addColorStop(.75, T.goldL); pg.addColorStop(1, T.gold);
      ctx.fillStyle = pg; ctx.fillRect(bx, by, bw, bh); ctx.restore();
      ctx.direction = "ltr"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
      font(ctx, 900, big, "Montserrat"); ctx.fillStyle = "#fff";
      ctx.save(); ctx.shadowColor = "rgba(0,0,0,.18)"; ctx.shadowBlur = 6; ctx.shadowOffsetY = 2;
      ctx.fillText(price, bx + 30, by + bh / 2 + 3); ctx.restore();
      ctx.fillStyle = "rgba(255,255,255,.7)"; ctx.fillRect(bx + 30 + vw + 20, by + bh / 2 - 29, 2, 58);
      font(ctx, 700, 21, "Montserrat"); spacing(ctx, 2.5); ctx.fillStyle = "#fff";
      ctx.fillText("QAR", bx + 30 + vw + 42, by + bh / 2 - 13);
      ctx.fillText(unit2, bx + 30 + vw + 42, by + bh / 2 + 15); spacing(ctx, 0);
    }
    ctx.restore();

    // --- facts row
    const specs = (c.specs || []).slice(0, 4);
    if (specs.length) {
      const x0 = 52, w = W - 104, h = 183, y0 = H - 150 - h, cw = w / specs.length;
      whiteCard(ctx, x0, y0, w, h);
      specs.forEach((s, i) => {
        // first fact on the right: it reads right-to-left in Arabic
        const cx = x0 + w - cw * (i + 0.5);
        if (i) { ctx.fillStyle = T.line; ctx.fillRect(x0 + w - cw * i, y0 + 1, 1, h - 2); }
        icon(ctx, s.icon, cx - 24, y0 + 20, 48, T.goldD);
        ctx.save(); ctx.textAlign = "center"; ctx.textBaseline = "alphabetic"; ctx.fillStyle = T.ink;
        let vs = 34; font(ctx, 800, vs, "Montserrat");
        while (textW(ctx, s.value) > cw - 20 && vs > 20) { vs -= 2; font(ctx, 800, vs, "Montserrat"); }
        if (/[؀-ۿ]/.test(s.value)) { font(ctx, 800, vs - 4, "Cairo"); ctx.direction = "rtl"; }
        ctx.fillText(s.value, cx, y0 + 108);
        ctx.direction = "rtl"; font(ctx, 700, 24, "Cairo"); ctx.fillText(s.ar, cx, y0 + 142);
        ctx.direction = "ltr"; font(ctx, 600, 14, "Montserrat"); spacing(ctx, 1.7); ctx.fillStyle = T.sub;
        ctx.fillText(String(s.en).toUpperCase(), cx + .85, y0 + 166); ctx.restore();
      });
    }

    if (sold) stamp(ctx, tag[0], tag[1]);
    footer(ctx);
  }

  function stamp(ctx, ar, en) {
    ctx.save();
    font(ctx, 900, 84, "Cairo"); const wa = textW(ctx, ar);
    const w = Math.max(wa, 260) + 88, h = 150;
    ctx.translate(W - 70 - w / 2, 760 + h / 2); ctx.rotate(-7 * Math.PI / 180);
    ctx.shadowColor = "rgba(20,18,15,.18)"; ctx.shadowBlur = 30; ctx.shadowOffsetY = 14;
    ctx.fillStyle = "rgba(255,255,255,.94)"; ctx.fillRect(-w / 2, -h / 2, w, h);
    ctx.shadowColor = "transparent"; ctx.strokeStyle = T.gold; ctx.lineWidth = 5;
    ctx.strokeRect(-w / 2 + 2.5, -h / 2 + 2.5, w - 5, h - 5);
    ctx.textAlign = "center"; ctx.direction = "rtl"; ctx.fillStyle = T.ink; ctx.fillText(ar, 0, 18);
    ctx.direction = "ltr"; font(ctx, 800, 26, "Montserrat"); spacing(ctx, 8.3); ctx.fillStyle = T.goldTxt;
    ctx.fillText(en, 4, 56); ctx.restore();
  }


  // ------------------------------------------------ story / video pieces
  const TAGS = { rent: ["للإيجار", "FOR RENT"], sale: ["للبيع", "FOR SALE"],
                 rented: ["تم التأجير", "RENTED"], sold: ["تم البيع", "SOLD"] };

  // Gold price block. x is its left edge; returns its width and height.
  function priceBox(ctx, c, x, y, big, measureOnly) {
    const price = fmtPrice(c.price);
    if (!price) return { w: 0, h: 0 };
    ctx.save();
    font(ctx, 900, big, "Montserrat"); const vw = textW(ctx, price);
    const us = Math.round(big * 0.27);
    font(ctx, 700, us, "Montserrat"); spacing(ctx, us * 0.12);
    const unit2 = c.type === "sale" || c.type === "sold" ? "TOTAL" : "/ MONTH";
    const uw = Math.max(textW(ctx, "QAR"), textW(ctx, unit2)); spacing(ctx, 0);
    const pad = Math.round(big * 0.38);
    const bw = pad + vw + 20 + 2 + 20 + uw + pad, bh = big + Math.round(big * 0.6);
    if (measureOnly) { ctx.restore(); return { w: bw, h: bh }; }
    ctx.shadowColor = "rgba(156,111,30,.25)"; ctx.shadowBlur = 24; ctx.shadowOffsetY = 10;
    ctx.fillStyle = goldGrad(ctx, x, x + bw); ctx.fillRect(x, y, bw, bh);
    ctx.shadowColor = "transparent";
    ctx.direction = "ltr"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
    font(ctx, 900, big, "Montserrat"); ctx.fillStyle = "#fff";
    ctx.save(); ctx.shadowColor = "rgba(0,0,0,.18)"; ctx.shadowBlur = 6; ctx.shadowOffsetY = 2;
    ctx.fillText(price, x + pad, y + bh / 2 + 3); ctx.restore();
    ctx.fillStyle = "rgba(255,255,255,.7)"; ctx.fillRect(x + pad + vw + 20, y + bh / 2 - big * .36, 2, big * .72);
    font(ctx, 700, us, "Montserrat"); spacing(ctx, us * 0.12); ctx.fillStyle = "#fff";
    ctx.fillText("QAR", x + pad + vw + 42, y + bh / 2 - us * .62);
    ctx.fillText(unit2, x + pad + vw + 42, y + bh / 2 + us * .72);
    ctx.restore();
    return { w: bw, h: bh };
  }

  // Pin + Arabic area name, English under it, right-aligned at R.
  function areaLine(ctx, c, R, y, size) {
    if (!(c.areaAr || c.areaEn)) return 0;
    ctx.save(); ctx.direction = "rtl"; ctx.textAlign = "right"; ctx.textBaseline = "alphabetic";
    icon(ctx, "pin", R - size * 1.3, y + 2, size * 1.3, T.goldD, 1.8);
    font(ctx, 700, size, "Cairo"); ctx.fillStyle = T.ink;
    ctx.fillText(c.areaAr || c.areaEn, R - size * 1.65, y + size);
    let h = size + 8;
    if (c.areaAr && c.areaEn) {
      ctx.direction = "ltr"; font(ctx, 600, Math.round(size * .62), "Montserrat"); ctx.fillStyle = T.sub;
      ctx.fillText(c.areaEn, R - size * 1.65, y + size * 1.9); h = size * 2;
    }
    ctx.restore();
    return h;
  }

  // Arabic title (up to 2 lines) + spaced English line, right-aligned at R.
  function titleBlock(ctx, c, R, y, maxW, max, min, draw) {
    ctx.save(); ctx.direction = "rtl"; ctx.textAlign = "right"; ctx.textBaseline = "alphabetic";
    const fit = fitLines(ctx, c.titleAr || "", 900, max, min, "Cairo", maxW, 2);
    let es = Math.round(max * .34), enW = 0;
    for (; es >= 14; es--) {
      font(ctx, 600, es, "Montserrat"); spacing(ctx, es * 0.26);
      enW = textW(ctx, c.titleEn || ""); if (enW <= maxW) break;
    }
    spacing(ctx, 0);
    let yy = y;
    font(ctx, 900, fit.size, "Cairo"); ctx.fillStyle = T.ink;
    for (const l of fit.lines) { yy += fit.size; if (draw) ctx.fillText(l, R, yy); yy += Math.round(fit.size * .18); }
    if (c.titleEn) {
      yy += es + 24;
      if (draw) {
        ctx.direction = "ltr"; font(ctx, 600, es, "Montserrat"); spacing(ctx, es * 0.26); ctx.fillStyle = T.sub;
        ctx.fillText(c.titleEn, R + es * 0.26, yy); spacing(ctx, 0);
      }
    }
    ctx.restore();
    return yy - y;
  }

  // Listing story: photo on top, details on calm ground below.
  function listingStory(ctx, c, photos) {
    const PH = 1080;
    ctx.fillStyle = T.bg; ctx.fillRect(0, 0, W, H);
    if (photos[0]) {
      ctx.save(); ctx.filter = "brightness(1.1) contrast(1.04)";
      cover(ctx, photos[0], 0, 0, W, PH, c.photoPos == null ? 0.5 : c.photoPos); ctx.restore();
    }
    let g = ctx.createLinearGradient(0, 0, 0, 560);
    g.addColorStop(0, "rgba(250,248,244,.95)"); g.addColorStop(.62, "rgba(250,248,244,.7)");
    g.addColorStop(1, "rgba(250,248,244,0)");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, 560);
    g = ctx.createLinearGradient(0, PH - 300, 0, PH);
    g.addColorStop(0, "rgba(250,248,244,0)"); g.addColorStop(1, T.bg);
    ctx.fillStyle = g; ctx.fillRect(0, PH - 300, W, 301);

    if (photos[1] || photos[2]) accent(ctx, -40, 700, 640, 260, -12);
    if (photos[1]) framedPhoto(ctx, photos[1], 60, 660, 330, 236, -3);
    if (photos[2]) framedPhoto(ctx, photos[2], 330, 760, 250, 180, 2.5);

    const tag = TAGS[c.type] || TAGS.rent;
    header(ctx, tag[0], tag[1]);

    const R = W - 66;
    // Anchor the text to the facts row: measure it, then start it so that
    // it ends just above the row, whatever the title's length.
    const specsTop = F.foot - 26 - 148 - 30;
    const th = titleBlock(ctx, c, R, 0, W - 132, 74, 50, false);
    const ahM = c.areaAr || c.areaEn ? (c.areaAr && c.areaEn ? 68 : 42) : 0;
    const rowM = Math.max(ahM, priceBox(ctx, c, 0, 0, 64, true).h);
    let y = Math.max(860, specsTop - rowM - 26 - 25 - th - 60);
    roof(ctx, R - 170, y, 170, 52, 7); y += 60;
    y += titleBlock(ctx, c, R, y, W - 132, 74, 50, true);
    y += 22; ctx.fillStyle = goldGrad(ctx, R - 120, R); ctx.fillRect(R - 120, y, 120, 3); y += 3;

    // Area on the right, price block on the left, on one row.
    y += 26;
    const ah = areaLine(ctx, c, R, y, 34);
    const pb = priceBox(ctx, c, 66, y, 64, true);
    if (pb.w) priceBox(ctx, c, 66, y + Math.max(0, (ah - pb.h) / 2), 64);
    y += Math.max(ah, pb.h);

    const specs = (c.specs || []).slice(0, 4);
    if (specs.length) {
      const x0 = 52, w = W - 104, h = 148, y0 = F.foot - 26 - h, cw = w / specs.length;
      whiteCard(ctx, x0, y0, w, h);
      specs.forEach((s, i) => {
        const cx = x0 + w - cw * (i + 0.5);
        if (i) { ctx.fillStyle = T.line; ctx.fillRect(x0 + w - cw * i, y0 + 1, 1, h - 2); }
        icon(ctx, s.icon, cx - 20, y0 + 14, 40, T.goldD);
        ctx.save(); ctx.textAlign = "center"; ctx.textBaseline = "alphabetic"; ctx.fillStyle = T.ink;
        let vs = 30; font(ctx, 800, vs, "Montserrat");
        while (textW(ctx, s.value) > cw - 20 && vs > 18) { vs -= 2; font(ctx, 800, vs, "Montserrat"); }
        if (/[؀-ۿ]/.test(s.value)) { font(ctx, 800, vs - 4, "Cairo"); ctx.direction = "rtl"; }
        ctx.fillText(s.value, cx, y0 + 88);
        ctx.direction = "rtl"; font(ctx, 700, 22, "Cairo"); ctx.fillText(s.ar, cx, y0 + 120);
        ctx.direction = "ltr"; font(ctx, 600, 12, "Montserrat"); spacing(ctx, 1.5); ctx.fillStyle = T.sub;
        ctx.fillText(String(s.en).toUpperCase(), cx + .75, y0 + 139); ctx.restore();
      });
    }
    if (c.type === "rented" || c.type === "sold") stampAt(ctx, tag[0], tag[1], 330, 790);
    footer(ctx);
  }

  function stampAt(ctx, ar, en, cx, cy) {
    ctx.save();
    font(ctx, 900, 84, "Cairo"); const wa = textW(ctx, ar);
    const w = Math.max(wa, 260) + 88, h = 150;
    ctx.translate(cx, cy); ctx.rotate(-7 * Math.PI / 180);
    ctx.shadowColor = "rgba(20,18,15,.18)"; ctx.shadowBlur = 30; ctx.shadowOffsetY = 14;
    ctx.fillStyle = "rgba(255,255,255,.94)"; ctx.fillRect(-w / 2, -h / 2, w, h);
    ctx.shadowColor = "transparent"; ctx.strokeStyle = T.gold; ctx.lineWidth = 5;
    ctx.strokeRect(-w / 2 + 2.5, -h / 2 + 2.5, w - 5, h - 5);
    ctx.textAlign = "center"; ctx.direction = "rtl"; ctx.fillStyle = T.ink; ctx.fillText(ar, 0, 18);
    ctx.direction = "ltr"; font(ctx, 800, 26, "Montserrat"); spacing(ctx, 8.3); ctx.fillStyle = T.goldTxt;
    ctx.fillText(en, 4, 56); ctx.restore();
  }

  // Transparent frame laid over a reel or video story: the header on a soft
  // light wash at the top, and one details panel above the caption area.
  function overlay(ctx, c) {
    let g = ctx.createLinearGradient(0, 0, 0, 470);
    g.addColorStop(0, "rgba(250,248,244,.94)"); g.addColorStop(.55, "rgba(250,248,244,.78)");
    g.addColorStop(1, "rgba(250,248,244,0)");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, 470);
    const series = { tip: ["نصائح", "TIPS"], area: ["دليل المناطق", "AREA GUIDE"],
                     market: ["السوق العقاري", "MARKET"], news: NEWS_TAG }[c.type] || TAGS[c.type] || TAGS.rent;
    header(ctx, series[0], series[1]);

    const x0 = 48, pw = 852, R = x0 + pw - 34, inner = pw - 68;
    let t = c;
    if (c.type === "area") t = { titleAr: c.nameAr, titleEn: String(c.nameEn || "").toUpperCase() };
    if (c.type === "market") t = { titleAr: c.titleAr, titleEn: [c.figure, c.figureLabel].filter(Boolean).join(" · ").toUpperCase() };
    if (c.type === "tip" || c.type === "news") t = { titleAr: c.titleAr, titleEn: c.labelEn ? String(c.labelEn).toUpperCase() : "" };
    const listing = !!TAGS[c.type];
    // Measure first, so the panel can sit with its bottom at y=1470.
    const th = titleBlock(ctx, t, R, 0, inner, 58, 40, false);
    const ah = listing ? (c.areaAr || c.areaEn ? 34 * 2 : 0) : 0;
    const pb = listing ? priceBox(ctx, c, 0, 0, 50, true) : { w: 0, h: 0 };
    const row = Math.max(ah, pb.h);
    const ph = 34 + 44 + 14 + th + (row ? 26 + row : 0) + 34;
    const y0 = 1470 - ph;
    ctx.save();
    ctx.shadowColor = "rgba(20,18,15,.16)"; ctx.shadowBlur = 36; ctx.shadowOffsetY = 12;
    ctx.fillStyle = "rgba(250,248,244,.96)"; ctx.fillRect(x0, y0, pw, ph); ctx.restore();
    ctx.fillStyle = goldGrad(ctx, x0, x0 + pw); ctx.fillRect(x0, y0, pw, 4);
    let y = y0 + 34;
    roof(ctx, R - 140, y, 140, 44, 6); y += 44 + 14;
    y += titleBlock(ctx, t, R, y, inner, 58, 40, true);
    if (row) {
      y += 26;
      areaLine(ctx, c, R, y + (row - ah) / 2, 30);
      if (pb.w) priceBox(ctx, c, x0 + 34, y + (row - pb.h) / 2, 50);
    }
  }

  // ---------------------------------------------------------------- tip
  function tip(ctx, c) {
    ctx.fillStyle = T.bg; ctx.fillRect(0, 0, W, H);
    const story = H > 1400;
    accent(ctx, W - 540, F.foot - 332, 620, 300, 12);
    header(ctx, "نصائح", "TIPS");
    ctx.save(); ctx.direction = "rtl"; ctx.textAlign = "right";
    const R = W - 90; let y = story ? 600 : 300;
    font(ctx, 900, 170, "Montserrat"); ctx.direction = "ltr";
    const num = String(c.number || "01"); const nw = textW(ctx, num);
    ctx.fillStyle = goldGrad(ctx, R - nw, R); ctx.fillText(num, R, y + 140); ctx.direction = "rtl";
    y += 170 + 20; roof(ctx, R - 170, y, 170, 52, 7); y += 52 + 18;
    const tf = fitLines(ctx, c.titleAr, 900, 76, 52, "Cairo", W - 180, 2); ctx.fillStyle = T.ink;
    for (const l of tf.lines) { y += tf.size + 4; ctx.fillText(l, R, y); y += 15; }
    font(ctx, 600, 36, "Cairo"); ctx.fillStyle = T.sub; y += 10;
    for (const l of wrap(ctx, c.bodyAr, W - 180).slice(0, story ? 6 : 4)) { y += 62; ctx.fillText(l, R, y); }
    if (c.labelEn) {
      ctx.direction = "ltr"; font(ctx, 700, 22, "Montserrat"); spacing(ctx, 5.3); ctx.fillStyle = T.goldTxt;
      y += 60; ctx.fillText(c.labelEn.toUpperCase(), R + 5.3, y); spacing(ctx, 0);
    }
    if (c.source) sourceLine(ctx, c.source, R, y + 40, "right");
    ctx.restore();
    const cy = F.foot - 52 - 104;
    whiteCard(ctx, 60, cy, W - 120, 104);
    icon(ctx, "chat", W - 60 - 34 - 44, cy + 30, 44, T.goldD);
    ctx.save(); ctx.direction = "rtl"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
    font(ctx, 600, 28, "Cairo"); ctx.fillStyle = T.ink;
    ctx.fillText(c.ctaAr || "عندك سؤال؟ تواصل معنا وفريقنا يساعدك.", W - 60 - 34 - 44 - 18, cy + 52);
    ctx.restore();
    footer(ctx);
  }

  // --------------------------------------------------------------- area
  function area(ctx, c, photos) {
    const img = photos[0];
    ctx.fillStyle = T.bg; ctx.fillRect(0, 0, W, H);
    const story = H > 1400, PH = story ? 1240 : 720;
    ctx.save(); ctx.filter = "brightness(1.08)"; cover(ctx, img, 0, 0, W, PH); ctx.restore();
    let g = ctx.createLinearGradient(0, 0, 0, PH);
    g.addColorStop(0, "rgba(250,248,244,.9)"); g.addColorStop(story ? .4 : .28, "rgba(250,248,244,0)");
    g.addColorStop(.7, "rgba(250,248,244,0)"); g.addColorStop(1, T.bg);
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, PH + 1);
    if (story) {               // the header sits lower on a story: wash further down
      g = ctx.createLinearGradient(0, 0, 0, 560);
      g.addColorStop(0, "rgba(250,248,244,.9)"); g.addColorStop(.6, "rgba(250,248,244,.6)");
      g.addColorStop(1, "rgba(250,248,244,0)");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, 560);
    }
    header(ctx, "دليل المناطق", "AREA GUIDE");
    ctx.save(); ctx.direction = "rtl"; ctx.textAlign = "right";
    const R = W - 66; let y = story ? 1010 : 560;
    roof(ctx, R - 190, y, 190, 58, 7); y += 62;
    font(ctx, 900, 128, "Cairo"); ctx.fillStyle = T.ink;
    let size = 128; while (textW(ctx, c.nameAr) > W - 132 && size > 70) { size -= 6; font(ctx, 900, size, "Cairo"); }
    y += size * 0.95; ctx.fillText(c.nameAr || "", R, y);
    ctx.direction = "ltr"; font(ctx, 800, 32, "Montserrat"); spacing(ctx, 10.9); ctx.fillStyle = T.goldTxt;
    y += 50; ctx.fillText(String(c.nameEn || "").toUpperCase(), R + 10.9, y); spacing(ctx, 0);
    ctx.restore();
    const facts = (c.facts || []).slice(0, 3);
    if (facts.length) {
      const gap = 16, fw = (W - 132 - gap * (facts.length - 1)) / facts.length, fy = y + 34, fh = 112;
      facts.forEach((f, i) => {
        const fx = W - 66 - (i + 1) * fw - i * gap;
        whiteCard(ctx, fx, fy, fw, fh);
        icon(ctx, f.icon || "pin", fx + fw - 18 - 40, fy + 22, 40, T.goldD);
        ctx.save(); ctx.direction = "rtl"; ctx.textAlign = "right";
        font(ctx, 600, 22, "Cairo"); ctx.fillStyle = T.sub; ctx.fillText(f.label, fx + fw - 72, fy + 44);
        let vs = 28; font(ctx, 800, vs, "Cairo");
        while (textW(ctx, f.value) > fw - 90 && vs > 18) { vs -= 2; font(ctx, 800, vs, "Cairo"); }
        ctx.fillStyle = T.ink; ctx.fillText(f.value, fx + fw - 72, fy + 84); ctx.restore();
      });
    }
    footer(ctx);
  }


  // ---------------------------------------------------------------- news
  // Real estate news from an official source: date, headline, two lines,
  // and who issued it, on the light ground.
  const NEWS_TAG = ["أخبار عقارية", "REAL ESTATE NEWS"];

  // "SOURCE: ..." in small spaced capitals.
  function sourceLine(ctx, text, x, y, align) {
    ctx.save(); ctx.direction = "ltr"; ctx.textAlign = align || "left";
    let sz = 18; font(ctx, 600, sz, "Montserrat"); spacing(ctx, 2);
    const label = "SOURCE: " + String(text);
    while (textW(ctx, label) > W - 180 && sz > 12) { sz--; font(ctx, 600, sz, "Montserrat"); }
    ctx.fillStyle = "#8a8270"; ctx.fillText(label, align === "right" ? x + 2 : x, y);
    ctx.restore();
  }

  function news(ctx, c) {
    const story = H > 1400;
    ctx.fillStyle = T.bg; ctx.fillRect(0, 0, W, H);
    accent(ctx, W - 560, F.foot - 360, 640, 320, 12);
    header(ctx, NEWS_TAG[0], NEWS_TAG[1]);
    ctx.save(); ctx.direction = "rtl"; ctx.textAlign = "right";
    const R = W - 90;
    let y = story ? 540 : 300;
    // date chip
    if (c.date) {
      font(ctx, 700, 22, "Montserrat"); spacing(ctx, 3.5); ctx.direction = "ltr";
      const dw = textW(ctx, c.date) + 48;
      ctx.strokeStyle = T.gold; ctx.lineWidth = 2; ctx.strokeRect(R - dw, y, dw, 50);
      ctx.fillStyle = T.goldTxt; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(c.date, R - dw / 2 + 1.75, y + 26); spacing(ctx, 0);
      ctx.textAlign = "right"; ctx.textBaseline = "alphabetic"; ctx.direction = "rtl";
      y += 50 + 40;
    }
    icon(ctx, "news", R - 64, y, 64, T.goldD, 1.5); y += 64 + 26;
    roof(ctx, R - 170, y, 170, 52, 7); y += 52 + 16;
    const tf = fitLines(ctx, c.titleAr, 900, 72, 50, "Cairo", W - 180, 3); ctx.fillStyle = T.ink;
    for (const l of tf.lines) { y += tf.size + 4; ctx.fillText(l, R, y); y += 14; }
    font(ctx, 600, 34, "Cairo"); ctx.fillStyle = T.sub; y += 8;
    for (const l of wrap(ctx, c.bodyAr, W - 180).slice(0, story ? 6 : 3)) { y += 58; ctx.fillText(l, R, y); }
    if (c.labelEn) {
      ctx.direction = "ltr"; font(ctx, 700, 22, "Montserrat"); spacing(ctx, 5.3); ctx.fillStyle = T.goldTxt;
      y += 58; ctx.fillText(String(c.labelEn).toUpperCase(), R + 5.3, y); spacing(ctx, 0);
    }
    ctx.restore();
    if (c.source) {
      const cy = F.foot - 52 - 96;
      whiteCard(ctx, 60, cy, W - 120, 96);
      icon(ctx, "shield", W - 60 - 30 - 40, cy + 28, 40, T.goldD);
      ctx.save(); ctx.direction = "rtl"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
      font(ctx, 700, 24, "Cairo"); ctx.fillStyle = T.ink;
      ctx.fillText("من مصدر رسمي", W - 60 - 30 - 40 - 16, cy + 34);
      ctx.restore();
      ctx.save(); ctx.direction = "ltr"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
      let sz = 17; font(ctx, 600, sz, "Montserrat"); spacing(ctx, 1.5);
      const t = String(c.source).toUpperCase();
      while (textW(ctx, t) > W - 300 && sz > 11) { sz--; font(ctx, 600, sz, "Montserrat"); }
      ctx.fillStyle = T.sub; ctx.fillText(t, W - 60 - 30 - 40 - 16 + 1.5, cy + 66);
      ctx.restore();
    }
    footer(ctx);
  }

  // ------------------------------------------------------------- market
  function market(ctx, c) {
    ctx.fillStyle = T.bg; ctx.fillRect(0, 0, W, H);
    const story = H > 1400;
    accent(ctx, -80, story ? 600 : 260, 560, 300, -12);
    header(ctx, "السوق العقاري", "MARKET");
    ctx.save(); ctx.textAlign = "center";
    let y = story ? 640 : 280;
    let size = 250; font(ctx, 900, size, "Montserrat");
    while (textW(ctx, c.figure) > W - 160 && size > 110) { size -= 10; font(ctx, 900, size, "Montserrat"); }
    const fw = textW(ctx, c.figure);
    ctx.fillStyle = goldGrad(ctx, W / 2 - fw / 2, W / 2 + fw / 2);
    y += size * 0.82; ctx.fillText(c.figure || "", W / 2, y);
    if (c.figureLabel) {
      font(ctx, 700, 30, "Montserrat"); spacing(ctx, 7.8); ctx.fillStyle = T.sub;
      y += 56; ctx.fillText(String(c.figureLabel).toUpperCase(), W / 2 + 3.9, y); spacing(ctx, 0);
    }
    y += 36; roof(ctx, W / 2 - 85, y, 170, 52, 7); y += 52 + 20;
    ctx.direction = "rtl"; const mf = fitLines(ctx, c.titleAr, 900, 66, 48, "Cairo", W - 160, 2); ctx.fillStyle = T.ink;
    for (const l of mf.lines) { y += mf.size + 10; ctx.fillText(l, W / 2, y); y += 10; }
    font(ctx, 600, 32, "Cairo"); ctx.fillStyle = T.sub;
    for (const l of wrap(ctx, c.bodyAr, W - 200).slice(0, story ? 5 : 3)) { y += 54; ctx.fillText(l, W / 2, y); }
    if (c.source) {
      ctx.direction = "ltr"; font(ctx, 600, 18, "Montserrat"); spacing(ctx, 2.2); ctx.fillStyle = "#8a8270";
      y += 50; ctx.fillText("SOURCE: " + c.source, W / 2, y); spacing(ctx, 0);
    }
    ctx.restore();
    footer(ctx);
  }

  async function draw(card, canvas, format) {
    // Load the photos first: everything after this line runs without a
    // pause, so two drawings in different sizes can never mix their settings.
    const photos = await Promise.all((card.photos || []).slice(0, 3).map(loadImage));
    F = FORMATS[format] || FORMATS.post; W = F.W; H = F.H;
    const c = canvas || document.createElement("canvas");
    c.width = W; c.height = H;
    const ctx = c.getContext("2d");
    ctx.clearRect(0, 0, W, H);
    if (F === FORMATS.overlay) { overlay(ctx, card); return c; }
    const story = F === FORMATS.story;
    switch (card.type) {
      case "tip": tip(ctx, card); break;
      case "area": area(ctx, card, photos); break;
      case "market": market(ctx, card); break;
      case "news": news(ctx, card); break;
      default: (story ? listingStory : listing)(ctx, card, photos);
    }
    return c;
  }

  // An extra photo (4th and later) shown after the design: the photo fills
  // the frame, with the logo on a cream tab and the usual contact footer.
  async function photoSlide(url, canvas, format) {
    const img = await loadImage(url);
    F = FORMATS[format] || FORMATS.post; W = F.W; H = F.H;
    const c = canvas || document.createElement("canvas");
    c.width = W; c.height = H;
    const ctx = c.getContext("2d");
    ctx.fillStyle = T.bg; ctx.fillRect(0, 0, W, H);
    cover(ctx, img, 0, 0, W, F.foot, 0.5);
    if (LOGO) {
      const h = 104, w = h * LOGO.width / LOGO.height, pad = 22, top = format === "story" ? 120 : 44;
      ctx.save();
      ctx.shadowColor = "rgba(0,0,0,.18)"; ctx.shadowBlur = 18; ctx.shadowOffsetY = 4;
      ctx.fillStyle = "rgba(250,248,244,.94)";
      ctx.beginPath(); ctx.roundRect(44, top, w + pad * 2, h + pad * 2, 14); ctx.fill();
      ctx.restore();
      ctx.drawImage(LOGO, 44 + pad, top + pad, w, h);
    }
    ctx.fillStyle = goldGrad(ctx, 0, W); ctx.fillRect(0, F.foot - 6, W, 6);
    footer(ctx);
    return c;
  }

  window.PostCards = { ready, draw, photoSlide, FORMATS, W: 1080, H: 1350, setContact: (o) => Object.assign(CONTACT, o || {}) };
})();
