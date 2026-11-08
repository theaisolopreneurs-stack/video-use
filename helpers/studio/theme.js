// Colour themes for the Home screen and the Studio (the look of the editor, not of your video).
// Load it in <head> without defer, so the saved theme is on before the page paints. Any
// <select id="themeSel"> on the page becomes the theme picker.
(function () {
  const THEMES = [
    ["", [["auto", "Auto (Warm, follows your computer)"]]],
    ["Light", [["warm-light", "Warm"], ["classic-light", "Classic"], ["github-light", "GitHub Light"],
      ["solarized-light", "Solarized Light"], ["catppuccin-latte", "Catppuccin Latte"], ["rose-pine-dawn", "Rosé Pine Dawn"],
      ["gruvbox-light", "Gruvbox Light"]]],
    ["Dark", [["warm-dark", "Warm (dark)"], ["classic-dark", "Classic (dark)"], ["github-dark", "GitHub Dark"],
      ["solarized-dark", "Solarized Dark"], ["catppuccin-mocha", "Catppuccin Mocha"], ["rose-pine", "Rosé Pine"],
      ["gruvbox-dark", "Gruvbox Dark"], ["nord", "Nord"], ["dracula", "Dracula"], ["one-dark", "One Dark"],
      ["tokyo-night", "Tokyo Night"], ["monokai", "Monokai"], ["everforest", "Everforest"], ["high-contrast", "High contrast"]]],
  ];
  const ids = THEMES.flatMap(([, l]) => l.map(([v]) => v));
  const legacy = { light: "warm-light", dark: "warm-dark" };
  const get = () => { try { return localStorage.getItem("studio-theme"); } catch { return null; } };
  const set = (v) => { try { localStorage.setItem("studio-theme", v); } catch {} };
  function apply(t) {
    t = legacy[t] || t || "auto";
    if (!ids.includes(t)) t = "auto";
    if (t === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = t;
    return t;
  }
  const current = apply(get());
  window.applyTheme = (t) => { set(t); return apply(t); };
  document.addEventListener("DOMContentLoaded", () => {
    const sel = document.getElementById("themeSel");
    if (!sel) return;
    sel.innerHTML = THEMES.map(([g, list]) => {
      const opts = list.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
      return g ? `<optgroup label="${g}">${opts}</optgroup>` : opts;
    }).join("");
    sel.value = current;
    sel.onchange = () => window.applyTheme(sel.value);
  });
})();
