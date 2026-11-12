/* Motion-graphics templates for HyperFrames.
 *
 * helpers/graphics.py writes one composition per plan: every graphic gets a slot on a
 * packed timeline, this file builds its DOM and adds its GSAP tweens at that slot.
 * Each template: (g, box, tl, t0, dur) where g = the plan entry, box = a positioned
 * wrapper element, tl = the master timeline, t0 = slot start, dur = slot length.
 * Sizes use var(--u) = 1/1080 of the canvas's short side, so templates scale to any size.
 */
(function () {
  const U = (n) => `calc(${n} * var(--u))`;
  const el = (tag, cls, html) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html !== undefined) e.innerHTML = html;
    return e;
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // Shared entrance / exit so every graphic moves the same way.
  function inOut(tl, target, t0, dur, from = { y: 40, scale: 0.92 }) {
    tl.fromTo(target, { opacity: 0, ...from }, { opacity: 1, y: 0, x: 0, scale: 1, duration: 0.32, ease: "back.out(1.6)" }, t0);
    tl.to(target, { opacity: 0, y: -24, duration: 0.22, ease: "power2.in" }, t0 + Math.max(0.5, dur - 0.24));
  }
  // Counts text up (typing / numbers) in a way that is exact when the timeline is seeked.
  function typeInto(tl, node, text, at, seconds) {
    const o = { n: 0 };
    tl.to(o, { n: text.length, duration: seconds, ease: "none", onUpdate: () => (node.textContent = text.slice(0, Math.round(o.n))) }, at);
  }

  const T = {};

  // Big keyword / statement card.
  T.text_card = (g, box, tl, t0, dur) => {
    const c = el("div", "panel center", `<div class="big">${esc(g.text)}</div>${g.sub ? `<div class="sub">${esc(g.sub)}</div>` : ""}`);
    box.append(c); inOut(tl, c, t0, dur);
  };

  // Animated number with a label: {value, prefix, suffix, label, decimals}
  T.stat = (g, box, tl, t0, dur) => {
    const num = el("div", "stat-num");
    const c = el("div", "panel center");
    c.append(num); c.append(el("div", "sub", esc(g.label || "")));
    box.append(c); inOut(tl, c, t0, dur);
    const o = { v: 0 }, d = g.decimals || 0;
    const show = () => (num.textContent = `${g.prefix || ""}${o.v.toFixed(d)}${g.suffix || ""}`);
    show();
    tl.to(o, { v: Number(g.value) || 0, duration: Math.min(1.2, dur * 0.5), ease: "power2.out", onUpdate: show }, t0 + 0.2);
  };

  // Clock / timer card: {from: "60:00", to: "0:54", label}
  T.timer = (g, box, tl, t0, dur) => {
    const parse = (s) => String(s).split(":").reduce((a, b) => a * 60 + Number(b), 0);
    const fmt = (s) => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
    const num = el("div", "stat-num mono");
    const c = el("div", "panel center");
    if (g.label) c.append(el("div", "kicker", esc(g.label)));
    c.append(num);
    box.append(c); inOut(tl, c, t0, dur);
    const o = { s: parse(g.from ?? g.to) };
    const show = () => (num.textContent = fmt(o.s));
    show();
    tl.to(o, { s: parse(g.to), duration: Math.min(1.4, dur * 0.55), ease: "power3.inOut", onUpdate: show }, t0 + 0.25);
  };

  // Horizontal bar chart: {title, bars: [{label, value}], unit}
  T.bar_chart = (g, box, tl, t0, dur) => {
    const bars = g.bars || [];
    const max = Math.max(1, ...bars.map((b) => Number(b.value) || 0));
    const c = el("div", "panel");
    if (g.title) c.append(el("div", "kicker", esc(g.title)));
    box.append(c); inOut(tl, c, t0, dur);
    bars.forEach((b, i) => {
      const row = el("div", "bar-row", `<div class="bar-label">${esc(b.label)}</div><div class="bar-track"><div class="bar-fill"></div></div><div class="bar-val">${esc(b.value)}${esc(g.unit || "")}</div>`);
      c.append(row);
      tl.fromTo(row.querySelector(".bar-fill"), { width: "0%" }, { width: `${(100 * (Number(b.value) || 0)) / max}%`, duration: 0.6, ease: "power3.out" }, t0 + 0.25 + i * 0.15);
    });
  };

  // Checklist that ticks items in order: {title, items: []}
  T.checklist = (g, box, tl, t0, dur) => {
    const items = g.items || [];
    const c = el("div", "panel");
    if (g.title) c.append(el("div", "kicker", esc(g.title)));
    box.append(c); inOut(tl, c, t0, dur);
    const step = Math.min(0.6, (dur - 0.8) / Math.max(1, items.length));
    items.forEach((t, i) => {
      const row = el("div", "check-row", `<span class="tick">✓</span><span>${esc(t)}</span>`);
      c.append(row);
      const at = t0 + 0.3 + i * step;
      tl.fromTo(row, { opacity: 0, x: -30 }, { opacity: 1, x: 0, duration: 0.25, ease: "power2.out" }, at);
      tl.fromTo(row.querySelector(".tick"), { scale: 0 }, { scale: 1, duration: 0.3, ease: "back.out(3)" }, at + 0.12);
    });
  };

  // AI prompt box with typing: {text, placeholder}
  T.prompt_box = (g, box, tl, t0, dur) => {
    const c = el("div", "prompt", `<span class="ptext"></span><span class="caret"></span><span class="send">↑</span>`);
    box.append(c); inOut(tl, c, t0, dur);
    typeInto(tl, c.querySelector(".ptext"), g.text || "", t0 + 0.3, Math.min(dur * 0.55, 0.045 * (g.text || "").length + 0.2));
    tl.fromTo(c.querySelector(".send"), { scale: 1 }, { scale: 1.25, yoyo: true, repeat: 1, duration: 0.12 }, t0 + Math.min(dur - 0.5, dur * 0.7));
  };

  // Chat bubbles: {messages: [{from: "user"|"ai", text}]}
  T.chat = (g, box, tl, t0, dur) => {
    const msgs = g.messages || [];
    const c = el("div", "chat");
    box.append(c); inOut(tl, c, t0, dur, { y: 20, scale: 1 });
    const step = Math.min(0.9, (dur - 0.6) / Math.max(1, msgs.length));
    msgs.forEach((m, i) => {
      const b = el("div", `bubble ${m.from === "user" ? "me" : "them"}`, esc(m.text));
      c.append(b);
      tl.fromTo(b, { opacity: 0, y: 24, scale: 0.9 }, { opacity: 1, y: 0, scale: 1, duration: 0.28, ease: "back.out(1.8)" }, t0 + 0.2 + i * step);
    });
  };

  // App / code window: {title, lines: [], typed: true}
  T.app_window = (g, box, tl, t0, dur) => {
    const c = el("div", "window", `<div class="wbar"><i></i><i></i><i></i><span>${esc(g.title || "")}</span></div><div class="wbody"></div>`);
    box.append(c); inOut(tl, c, t0, dur);
    const body = c.querySelector(".wbody");
    const lines = g.lines || [];
    const step = Math.min(0.5, (dur - 0.8) / Math.max(1, lines.length));
    lines.forEach((ln, i) => {
      const row = el("div", "wline");
      body.append(row);
      if (g.typed === false) { row.textContent = ln; tl.fromTo(row, { opacity: 0 }, { opacity: 1, duration: 0.2 }, t0 + 0.3 + i * step); }
      else typeInto(tl, row, ln, t0 + 0.3 + i * step, step * 0.9);
    });
  };

  // Picture-in-picture proof insert: {src (copied into assets/), label}
  T.pip = (g, box, tl, t0, dur) => {
    const media = /\.(mp4|mov|webm)$/i.test(g.src || "")
      ? `<video src="${esc(g.src)}" muted playsinline class="clip" data-start="${t0}" data-duration="${dur}" data-track-index="5"></video>`
      : `<img src="${esc(g.src)}">`;
    const c = el("div", "pip", `${media}${g.label ? `<div class="pip-label">${esc(g.label)}</div>` : ""}`);
    box.append(c); inOut(tl, c, t0, dur, { y: 0, scale: 0.6, rotation: -4 });
  };

  // Collage of screenshots / posts: {srcs: [], title}
  T.collage = (g, box, tl, t0, dur) => {
    const srcs = (g.srcs || []).slice(0, 9);
    const c = el("div", "collage");
    if (g.title) box.append(el("div", "kicker center-text", esc(g.title)));
    box.append(c);
    srcs.forEach((s, i) => {
      const tile = el("div", "tile", `<img src="${esc(s)}">`);
      c.append(tile);
      tl.fromTo(tile, { opacity: 0, scale: 0.5, rotation: (i % 2 ? 6 : -6) }, { opacity: 1, scale: 1, rotation: 0, duration: 0.3, ease: "back.out(1.7)" }, t0 + 0.08 * i);
    });
    tl.to(box, { opacity: 0, duration: 0.22 }, t0 + Math.max(0.5, dur - 0.24));
  };

  // End card with a call to action and a mock comment: {text, comment, handle}
  T.cta = (g, box, tl, t0, dur) => {
    const c = el("div", "panel center", `<div class="big">${esc(g.text || "")}</div>${g.comment ? `<div class="comment"><span class="avatar"></span><span><b>${esc(g.handle || "you")}</b> ${esc(g.comment)}</span><span class="heart">♥</span></div>` : ""}`);
    box.append(c); inOut(tl, c, t0, dur);
    const cm = c.querySelector(".comment");
    if (cm) tl.fromTo(cm, { opacity: 0, y: 20 }, { opacity: 1, y: 0, duration: 0.3, ease: "power2.out" }, t0 + 0.6);
  };

  // Name / handle lower third: {name, handle}
  T.lower_third = (g, box, tl, t0, dur) => {
    const c = el("div", "lt", `<div class="lt-bar"></div><div><div class="lt-name">${esc(g.name || "")}</div><div class="lt-handle">${esc(g.handle || "")}</div></div>`);
    box.append(c);
    tl.fromTo(c.querySelector(".lt-bar"), { scaleY: 0 }, { scaleY: 1, duration: 0.25, ease: "power2.out" }, t0);
    tl.fromTo(c, { opacity: 0, x: -60 }, { opacity: 1, x: 0, duration: 0.35, ease: "power3.out" }, t0 + 0.1);
    tl.to(c, { opacity: 0, x: -40, duration: 0.22 }, t0 + Math.max(0.5, dur - 0.24));
  };

  // Emoji / icon pop: {emoji}
  T.emoji = (g, box, tl, t0, dur) => {
    const c = el("div", "emoji", esc(g.emoji || "🔥"));
    box.append(c);
    tl.fromTo(c, { opacity: 0, scale: 0, rotation: -20 }, { opacity: 1, scale: 1, rotation: 0, duration: 0.35, ease: "back.out(2.5)" }, t0);
    tl.to(c, { y: -10, duration: Math.max(0.2, dur - 0.6), ease: "sine.inOut" }, t0 + 0.35);
    tl.to(c, { opacity: 0, scale: 0.6, duration: 0.2 }, t0 + Math.max(0.5, dur - 0.22));
  };

  // Thin progress bar along the bottom for the whole video: {}
  T.progress = (g, box, tl, t0, dur) => {
    const c = el("div", "progress", `<div class="progress-fill"></div>`);
    box.append(c);
    tl.fromTo(c.querySelector(".progress-fill"), { width: "0%" }, { width: "100%", duration: dur, ease: "none" }, t0);
  };

  // ---- placement helpers for graphics that live at a screen edge, not in the upper area ----------
  const pin = (box, where) => {
    box.style.top = box.style.bottom = "auto";
    box.classList.add(`pin-${where}`);
    if (where === "full") { box.style.inset = "0"; box.style.alignItems = "stretch"; }
    else if (where === "top") box.style.top = U(70);
    else if (where === "bottom") box.style.bottom = "0";
  };
  const out = (tl, target, t0, dur, vars = { opacity: 0, y: -24 }) =>
    tl.to(target, { ...vars, duration: 0.22, ease: "power2.in" }, t0 + Math.max(0.5, dur - 0.24));

  // Rounded label boxes stacking in one under another: {lines: [], position}
  // ("Comment EDIT" / "to get access to these" / "+ 40 more effects"). Each line can be
  // {text, at} with at = seconds after the graphic starts; otherwise they come in evenly.
  T.pill_stack = (g, box, tl, t0, dur) => {
    const lines = (g.lines || []).map((l) => (typeof l === "string" ? { text: l } : l));
    const c = el("div", "pstack");
    box.append(c);
    const step = Math.min(0.8, (dur - 0.6) / Math.max(1, lines.length));
    lines.forEach((l, i) => {
      const p = el("div", `pill-line ${i === 0 ? "first" : ""}`, esc(l.text).replace(/\*(.+?)\*/g, "<b>$1</b>"));
      c.append(p);
      tl.fromTo(p, { opacity: 0, y: 26, scale: 0.7 }, { opacity: 1, y: 0, scale: 1, duration: 0.28, ease: "back.out(2.2)" }, t0 + (l.at ?? 0.1 + i * step));
    });
    out(tl, c, t0, dur);
  };

  // Small label boxes in the top-left corner: {items: ["20 video effects in 20 seconds", "built by *Claude*"]}
  T.pills = (g, box, tl, t0, dur) => {
    pin(box, "top");
    box.style.justifyContent = g.align === "center" ? "center" : "flex-start";
    const c = el("div", "pills");
    if (g.align !== "center") c.style.marginLeft = U(48);
    box.append(c);
    (g.items || []).forEach((t, i) => {
      const p = el("div", "pill-tag", esc(t).replace(/\*(.+?)\*/g, "<b>$1</b>"));
      c.append(p);
      tl.fromTo(p, { opacity: 0, x: -30 }, { opacity: 1, x: 0, duration: 0.3, ease: "power3.out" }, t0 + 0.1 + i * 0.18);
    });
    out(tl, c, t0, dur);
  };

  // A big word slammed down letter by letter, with a small word above it: {text: "STAMP", kicker: "word"}
  T.word_stamp = (g, box, tl, t0, dur) => {
    const c = el("div", "stamp");
    if (g.kicker) c.append(el("div", "stamp-kicker", esc(g.kicker)));
    const w = el("div", "stamp-word");
    c.append(w); box.append(c);
    const letters = [...String(g.text || "STAMP")];
    // Fit the word to the screen: big, but never off the edge.
    w.textContent = letters.join("");
    const room = (root.clientWidth || 1080) * 0.88;
    if (w.scrollWidth > room) w.style.fontSize = `${(parseFloat(getComputedStyle(w).fontSize) * room) / w.scrollWidth}px`;
    w.textContent = "";
    const step = Math.min(0.11, (dur * 0.45) / Math.max(1, letters.length));
    if (g.kicker) tl.fromTo(c.firstChild, { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 0.25 }, t0);
    letters.forEach((ch, i) => {
      const s = el("span", "", ch === " " ? "&nbsp;" : esc(ch));
      w.append(s);
      tl.fromTo(s, { opacity: 0, scale: 2.6, y: -30 }, { opacity: 1, scale: 1, y: 0, duration: 0.16, ease: "power4.in" }, t0 + 0.15 + i * step);
      tl.fromTo(w, { x: 0 }, { x: i % 2 ? -4 : 4, yoyo: true, repeat: 1, duration: 0.04 }, t0 + 0.31 + i * step);
    });
    out(tl, c, t0, dur, { opacity: 0, scale: 1.08 });
  };

  // An Instagram-style comment being typed, posted, then dropping in as a real comment, and the
  // reply under it: {text: "EDIT", reply: "EDIT to get access to these + 40 more", handle, replier, likes}
  T.comment_drop = (g, box, tl, t0, dur) => {
    pin(box, "top");
    const c = el("div", "cdrop");
    const input = el("div", "cd-input", `<span class="cd-av"></span><span class="cd-text"><span class="cd-ph">Add a comment…</span><span class="cd-typed"></span></span><span class="cd-post">Post</span>`);
    const posted = el("div", "cd-card", `<span class="cd-av"></span><div class="cd-body"><div class="cd-meta"><b>${esc(g.replier || g.handle || "you")}</b> · now</div><div>${esc(g.reply || g.text || "EDIT")}</div></div><span class="cd-heart">♥<small>${esc(g.likes ?? 8)}</small></span>`);
    const mine = el("div", "cd-card small", `<span class="cd-av"></span><div class="cd-body"><div class="cd-meta"><b>${esc(g.handle || "you")}</b> · now</div><div><b>${esc(g.text || "EDIT")}</b></div></div><span class="cd-heart">♡</span>`);
    c.append(input, posted, mine); box.append(c);
    tl.fromTo(input, { opacity: 0, y: -30 }, { opacity: 1, y: 0, duration: 0.3, ease: "power3.out" }, t0);
    const typedAt = t0 + 0.35, text = String(g.text || "EDIT"), typeDur = Math.min(0.6, 0.12 * text.length);
    tl.set(input.querySelector(".cd-ph"), { display: "none" }, typedAt);
    typeInto(tl, input.querySelector(".cd-typed"), text, typedAt, typeDur);
    const post = input.querySelector(".cd-post");
    tl.to(post, { scale: 1.2, yoyo: true, repeat: 1, duration: 0.1 }, typedAt + typeDur + 0.1);
    tl.to(input, { opacity: 0, height: 0, paddingTop: 0, paddingBottom: 0, marginBottom: 0, duration: 0.2 }, typedAt + typeDur + 0.3);
    tl.fromTo(posted, { opacity: 0, y: -40, scale: 0.9 }, { opacity: 1, y: 0, scale: 1, duration: 0.35, ease: "back.out(1.8)" }, typedAt + typeDur + 0.35);
    tl.fromTo(mine, { opacity: 0, y: -20 }, { opacity: 1, y: 0, duration: 0.3, ease: "power3.out" }, typedAt + typeDur + 0.75);
    const hearts = posted.querySelector(".cd-heart");
    tl.fromTo(hearts, { scale: 1 }, { scale: 1.35, yoyo: true, repeat: 1, duration: 0.12 }, typedAt + typeDur + 1.1);
    out(tl, c, t0, dur);
  };

  // A chat voice message: play button, a waveform that fills as it plays, a timer: {seconds: 7, label: "Voice note"}
  T.voice_note = (g, box, tl, t0, dur) => {
    pin(box, "top");
    const n = 34, secs = Number(g.seconds) || 7;
    const bars = Array.from({ length: n }, (_, i) => {
      const h = 18 + Math.round(62 * Math.abs(Math.sin(i * 1.7) * Math.cos(i * 0.6)));
      return `<i style="height:${U(h)}"></i>`;
    }).join("");
    const c = el("div", "vnote-wrap", `<div class="vnote"><span class="vn-play">▶</span><span class="vn-wave">${bars}</span><span class="vn-time">0:00</span><span class="vn-av"></span></div><div class="vn-label">${esc(g.label || "Voice note")}</div>`);
    box.append(c);
    tl.fromTo(c, { opacity: 0, y: -30, scale: 0.9 }, { opacity: 1, y: 0, scale: 1, duration: 0.32, ease: "back.out(1.7)" }, t0);
    const play = c.querySelector(".vn-play"), time = c.querySelector(".vn-time");
    tl.set(play, { textContent: "❚❚" }, t0 + 0.45);
    const wave = [...c.querySelectorAll(".vn-wave i")];
    const playFor = Math.max(0.6, dur - 1.2);
    wave.forEach((b, i) => tl.to(b, { backgroundColor: "#1d1d1b", duration: 0.05 }, t0 + 0.45 + (i / n) * playFor));
    const o = { s: 0 };
    tl.to(o, { s: secs, duration: playFor, ease: "none", onUpdate: () => (time.textContent = `0:${String(Math.floor(o.s)).padStart(2, "0")}`) }, t0 + 0.45);
    if (g.delivered !== false) {
      const d = el("div", "vn-delivered", "Delivered ✓"); c.append(d);
      tl.fromTo(d, { opacity: 0 }, { opacity: 1, duration: 0.2 }, t0 + 0.45 + playFor);
    }
    out(tl, c, t0, dur);
  };

  // A phone's incoming-call banner, then declined: {name: "Incoming call", sub: "mobile", declined: true}
  T.incoming_call = (g, box, tl, t0, dur) => {
    pin(box, "top");
    const c = el("div", "call", `<span class="call-av">👤</span><div class="call-txt"><b>${esc(g.name || "Incoming call")}</b><small>${esc(g.sub || "mobile")}</small></div><span class="call-btn no">✕</span><span class="call-btn yes">✆</span>`);
    box.append(c);
    tl.fromTo(c, { opacity: 0, y: -120 }, { opacity: 1, y: 0, duration: 0.4, ease: "power3.out" }, t0);
    tl.to(c.querySelector(".call-btn.yes"), { scale: 1.12, yoyo: true, repeat: 5, duration: 0.18 }, t0 + 0.4);
    if (g.declined !== false) {
      const at = t0 + Math.max(0.9, dur * 0.6);
      const chip = el("div", "call-declined", `<span>✕</span> ${esc(g.declined_text || "Call declined")}`);
      box.append(chip);
      tl.to(c.querySelector(".call-btn.no"), { scale: 0.85, yoyo: true, repeat: 1, duration: 0.08 }, at - 0.15);
      tl.to(c, { opacity: 0, y: -60, duration: 0.25, ease: "power2.in" }, at);
      tl.fromTo(chip, { opacity: 0, y: -10 }, { opacity: 1, y: 0, duration: 0.25 }, at + 0.1);
      out(tl, chip, t0, dur);
    } else out(tl, c, t0, dur);
  };

  // A phone keyboard typing a phrase, keys lighting up, word suggestions above: {text: "Predict keys"}
  T.keyboard = (g, box, tl, t0, dur) => {
    pin(box, "bottom");
    const rows = ["qwertyuiop", "asdfghjkl", "zxcvbnm"];
    const keyHtml = rows.map((r, i) => `<div class="kb-row r${i}">${i === 2 ? '<span class="kb-key wide">⇧</span>' : ""}${[...r].map((ch) => `<span class="kb-key" data-k="${ch}">${ch}</span>`).join("")}${i === 2 ? '<span class="kb-key wide">⌫</span>' : ""}</div>`).join("");
    const c = el("div", "kb", `<div class="kb-field"><span class="kb-typed"></span><span class="caret"></span><span class="kb-send">↑</span></div><div class="kb-sugg"><span></span><span></span><span></span></div>${keyHtml}<div class="kb-row"><span class="kb-key wide">123</span><span class="kb-key space">space</span><span class="kb-key wide">return</span></div>`);
    box.append(c);
    tl.fromTo(c, { y: "100%" }, { y: "0%", duration: 0.35, ease: "power3.out" }, t0);
    const text = String(g.text || "Predict keys");
    const typed = c.querySelector(".kb-typed"), sugg = [...c.querySelectorAll(".kb-sugg span")];
    const words = text.split(/\s+/), extra = (g.suggestions || ["the", "your", "what", "keys", "keep", "keyboard", "this", "next"]);
    const step = Math.min(0.16, (dur - 1.1) / Math.max(1, text.length));
    [...text].forEach((ch, i) => {
      const at = t0 + 0.45 + i * step;
      tl.set(typed, { textContent: text.slice(0, i + 1) }, at);
      const k = c.querySelector(`.kb-key[data-k="${ch.toLowerCase()}"]`) || c.querySelector(".kb-key.space");
      tl.fromTo(k, { backgroundColor: "#ffffff", scale: 1 }, { backgroundColor: "#c9ccd3", scale: 1.25, yoyo: true, repeat: 1, duration: 0.05 }, at);
      // suggestions: the word being typed in the middle, two guesses either side
      const sofar = text.slice(0, i + 1).split(/\s+/).pop();
      const done = words.find((w) => w.toLowerCase().startsWith(sofar.toLowerCase())) || sofar;
      const pick = [sofar, done, extra[(i * 3) % extra.length]];
      sugg.forEach((s, j) => tl.set(s, { textContent: pick[j] }, at));  // what you typed | best guess | another
    });
    tl.to(c, { y: "100%", duration: 0.25, ease: "power2.in" }, t0 + Math.max(0.6, dur - 0.27));
  };

  // A file dragged into a drop zone, becoming a video card with a label:
  // {name: "2026-11-10 13-34-12.mov", size: "24.3 MB", label: "0:32 of raw footage", src (picture)}
  T.file_drop = (g, box, tl, t0, dur) => {
    const c = el("div", "fdrop", `<div class="fd-zone"><div class="fd-icon">⤓</div><div>${esc(g.zone || "Drop raw footage")}</div></div>
      <div class="fd-file">${g.src ? `<img src="${esc(g.src)}">` : '<span class="fd-thumb"></span>'}<div><b>${esc(g.name || "raw-footage.mov")}</b><small>${esc(g.size || "")}</small></div></div>
      <div class="fd-card">${g.src ? `<img src="${esc(g.src)}">` : ""}<span class="fd-label">${esc(g.label || "")}</span></div>`);
    box.append(c);
    const zone = c.querySelector(".fd-zone"), file = c.querySelector(".fd-file"), card = c.querySelector(".fd-card");
    tl.fromTo(zone, { opacity: 0, scale: 0.9 }, { opacity: 1, scale: 1, duration: 0.3, ease: "power3.out" }, t0);
    tl.fromTo(file, { opacity: 0, x: -360, y: 140, rotation: -8 }, { opacity: 1, x: 0, y: 0, rotation: 0, duration: 0.5, ease: "power3.out" }, t0 + 0.35);
    tl.to(zone, { borderColor: "var(--accent)", duration: 0.15 }, t0 + 0.7);
    tl.to(file, { opacity: 0, scale: 0.6, duration: 0.2 }, t0 + 1.05);
    tl.to(zone, { opacity: 0, duration: 0.2 }, t0 + 1.05);
    tl.fromTo(card, { opacity: 0, scale: 0.7 }, { opacity: 1, scale: 1, duration: 0.35, ease: "back.out(1.6)" }, t0 + 1.1);
    if (g.label) tl.fromTo(card.querySelector(".fd-label"), { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 0.25 }, t0 + 1.4);
    out(tl, c, t0, dur);
  };

  // A film-script page typing out what you say, with a highlighted line:
  // {heading: "INT. GREEN ROOM - DAY", character: "JUSTYN", paren: "(to camera)", text: "...", highlight: "Screenplay page."}
  T.screenplay = (g, box, tl, t0, dur) => {
    pin(box, "full");
    const c = el("div", "script-page", `<div class="sp-inner"><div class="sp-head"></div><div class="sp-char">${esc(g.character || "YOU")}</div><div class="sp-paren">${esc(g.paren || "(to camera)")}</div><div class="sp-line"></div><div class="sp-cut">${esc(g.cut || "CUT TO:")}</div></div>`);
    box.append(c);
    tl.fromTo(c, { opacity: 0 }, { opacity: 1, duration: 0.15 }, t0);
    const head = String(g.heading || "INT. STUDIO - DAY"), line = String(g.text || "");
    typeInto(tl, c.querySelector(".sp-head"), head, t0 + 0.1, Math.min(0.5, dur * 0.15));
    const body = c.querySelector(".sp-line");
    const hl = String(g.highlight || "");
    const o = { n: 0 };
    tl.to(o, { n: line.length, duration: Math.min(dur * 0.5, 0.035 * line.length + 0.3), ease: "none", onUpdate: () => {
      const s = line.slice(0, Math.round(o.n)), k = hl ? s.indexOf(hl) : -1;
      body.innerHTML = k < 0 ? esc(s) : `${esc(s.slice(0, k))}<mark>${esc(hl)}</mark>${esc(s.slice(k + hl.length))}`;
    } }, t0 + 0.55);
    tl.fromTo(c.querySelectorAll(".sp-char, .sp-paren"), { opacity: 0 }, { opacity: 1, duration: 0.15, stagger: 0.1 }, t0 + 0.4);
    tl.fromTo(c.querySelector(".sp-cut"), { opacity: 0 }, { opacity: 1, duration: 0.2 }, t0 + Math.max(0.6, dur * 0.7));
    tl.to(c, { opacity: 0, duration: 0.18 }, t0 + Math.max(0.5, dur - 0.2));
  };

  // A framed silent-film title card. The video closes down to a circle at the centre and opens
  // back up after: {text: "Intertitle.", sub}
  T.intertitle = (g, box, tl, t0, dur) => {
    pin(box, "full");
    const c = el("div", "itl", `<div class="itl-frame"><span class="itl-orn top">◆</span><div class="itl-text">${esc(g.text || "Intertitle.")}</div>${g.sub ? `<div class="itl-sub">${esc(g.sub)}</div>` : ""}<span class="itl-orn bottom">◆</span></div>`);
    box.append(c);
    const iris = { r: 110 };
    const setIris = () => { const m = `radial-gradient(circle at 50% 50%, transparent ${iris.r}%, #000 ${iris.r + 0.5}%)`; c.style.webkitMaskImage = m; c.style.maskImage = m; };
    setIris();
    tl.fromTo(iris, { r: 110 }, { r: 0, duration: 0.45, ease: "power2.in", onUpdate: setIris }, t0);
    tl.fromTo(c.querySelector(".itl-frame"), { opacity: 0, scale: 0.96 }, { opacity: 1, scale: 1, duration: 0.4, ease: "power2.out" }, t0 + 0.35);
    tl.fromTo(c.querySelector(".itl-text"), { opacity: 0, letterSpacing: "0.2em" }, { opacity: 1, letterSpacing: "0em", duration: 0.6, ease: "power2.out" }, t0 + 0.45);
    tl.fromTo(c.querySelectorAll(".itl-orn"), { scale: 0 }, { scale: 1, duration: 0.3, stagger: 0.1, ease: "back.out(3)" }, t0 + 0.55);
    tl.to(iris, { r: 110, duration: 0.45, ease: "power2.out", onUpdate: setIris }, t0 + Math.max(0.9, dur - 0.45));
  };

  window.HF_TEMPLATES = T;
  window.HF_TEMPLATE_NAMES = Object.keys(T);
})();
