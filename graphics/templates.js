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

  window.HF_TEMPLATES = T;
  window.HF_TEMPLATE_NAMES = Object.keys(T);
})();
