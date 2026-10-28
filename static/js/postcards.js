/* Planned Real Estate — "Gold Light" post designs, drawn on <canvas>.
 *
 * One file holds every post type so they stay one family: the same header
 * (logo left, series label right), the same gold roof line above headlines,
 * the same dark footer. Change a token here and every post follows.
 *
 *   await PostCards.ready(baseUrl)            // fonts + logo, once per page
 *   const c = await PostCards.draw(card)      // -> 1080x1350 <canvas>
 *
 * card.type: "rent" | "sale" | "rented" | "sold" | "tip" | "area" | "market"
 * Drawn in the browser so the picture you approve is exactly what is posted.
 */
(function () {
  "use strict";
  const W = 1080, H = 1350;
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
      ctx.drawImage(LOGO, 60, 56, w, h);
    }
    ctx.save();
    ctx.direction = "rtl"; ctx.textAlign = "right"; ctx.textBaseline = "alphabetic";
    font(ctx, 700, 40, "Cairo"); ctx.fillStyle = T.ink; ctx.fillText(tagAr, W - 60, 106);
    const wa = textW(ctx, tagAr);
    ctx.direction = "ltr"; font(ctx, 700, 20, "Montserrat"); spacing(ctx, 4.8);
    ctx.fillStyle = T.goldTxt; ctx.fillText(tagEn, W - 60 + 4.8, 138);
    const we = textW(ctx, tagEn);
    spacing(ctx, 0);
    const right = W - 60 - Math.max(wa, we) - 18;
    ctx.fillStyle = goldGrad(ctx, right - 120, right); ctx.fillRect(right - 120, 105, 120, 2);
    ctx.restore();
  }

  function footer(ctx) {
    const y = H - 118, cy = y + 59;
    ctx.save();
    ctx.fillStyle = T.dark; ctx.fillRect(0, y, W, 118);
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
  async function listing(ctx, c) {
    const photos = await Promise.all((c.photos || []).slice(0, 3).map(loadImage));
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

  // ---------------------------------------------------------------- tip
  function tip(ctx, c) {
    ctx.fillStyle = T.bg; ctx.fillRect(0, 0, W, H);
    accent(ctx, W - 540, 900, 620, 300, 12);
    header(ctx, "نصائح", "TIPS");
    ctx.save(); ctx.direction = "rtl"; ctx.textAlign = "right";
    const R = W - 90; let y = 300;
    font(ctx, 900, 170, "Montserrat"); ctx.direction = "ltr";
    const num = String(c.number || "01"); const nw = textW(ctx, num);
    ctx.fillStyle = goldGrad(ctx, R - nw, R); ctx.fillText(num, R, y + 140); ctx.direction = "rtl";
    y += 170 + 20; roof(ctx, R - 170, y, 170, 52, 7); y += 52 + 18;
    const tf = fitLines(ctx, c.titleAr, 900, 76, 52, "Cairo", W - 180, 2); ctx.fillStyle = T.ink;
    for (const l of tf.lines) { y += tf.size + 4; ctx.fillText(l, R, y); y += 15; }
    font(ctx, 600, 36, "Cairo"); ctx.fillStyle = T.sub; y += 10;
    for (const l of wrap(ctx, c.bodyAr, W - 180).slice(0, 4)) { y += 62; ctx.fillText(l, R, y); }
    if (c.labelEn) {
      ctx.direction = "ltr"; font(ctx, 700, 22, "Montserrat"); spacing(ctx, 5.3); ctx.fillStyle = T.goldTxt;
      y += 60; ctx.fillText(c.labelEn.toUpperCase(), R + 5.3, y); spacing(ctx, 0);
    }
    ctx.restore();
    const cy = H - 170 - 104;
    whiteCard(ctx, 60, cy, W - 120, 104);
    icon(ctx, "chat", W - 60 - 34 - 44, cy + 30, 44, T.goldD);
    ctx.save(); ctx.direction = "rtl"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
    font(ctx, 600, 28, "Cairo"); ctx.fillStyle = T.ink;
    ctx.fillText(c.ctaAr || "عندك سؤال؟ تواصل معنا وفريقنا يساعدك.", W - 60 - 34 - 44 - 18, cy + 52);
    ctx.restore();
    footer(ctx);
  }

  // --------------------------------------------------------------- area
  async function area(ctx, c) {
    const img = await loadImage((c.photos || [])[0]);
    ctx.fillStyle = T.bg; ctx.fillRect(0, 0, W, H);
    ctx.save(); ctx.filter = "brightness(1.08)"; cover(ctx, img, 0, 0, W, 720); ctx.restore();
    let g = ctx.createLinearGradient(0, 0, 0, 720);
    g.addColorStop(0, "rgba(250,248,244,.9)"); g.addColorStop(.28, "rgba(250,248,244,0)");
    g.addColorStop(.7, "rgba(250,248,244,0)"); g.addColorStop(1, T.bg);
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, 721);
    header(ctx, "دليل المناطق", "AREA GUIDE");
    ctx.save(); ctx.direction = "rtl"; ctx.textAlign = "right";
    const R = W - 66; let y = 560;
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

  // ------------------------------------------------------------- market
  function market(ctx, c) {
    ctx.fillStyle = T.bg; ctx.fillRect(0, 0, W, H);
    accent(ctx, -80, 260, 560, 300, -12);
    header(ctx, "السوق العقاري", "MARKET");
    ctx.save(); ctx.textAlign = "center";
    let y = 280;
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
    for (const l of wrap(ctx, c.bodyAr, W - 200).slice(0, 3)) { y += 54; ctx.fillText(l, W / 2, y); }
    if (c.source) {
      ctx.direction = "ltr"; font(ctx, 600, 18, "Montserrat"); spacing(ctx, 2.2); ctx.fillStyle = "#8a8270";
      y += 50; ctx.fillText("SOURCE: " + c.source, W / 2, y); spacing(ctx, 0);
    }
    ctx.restore();
    footer(ctx);
  }

  async function draw(card, canvas) {
    const c = canvas || document.createElement("canvas");
    c.width = W; c.height = H;
    const ctx = c.getContext("2d");
    ctx.clearRect(0, 0, W, H);
    switch (card.type) {
      case "tip": tip(ctx, card); break;
      case "area": await area(ctx, card); break;
      case "market": market(ctx, card); break;
      default: await listing(ctx, card);
    }
    return c;
  }

  window.PostCards = { ready, draw, W, H, setContact: (o) => Object.assign(CONTACT, o || {}) };
})();
