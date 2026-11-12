// The Claude account button, on the Home screen and in the Studio: always in the top bar, so connecting
// Claude (needed for the "Tell Claude" box) is never hidden. Any <button id="claudeAcct"> becomes it.
//   window.connectClaude()   opens Claude's sign-in and waits until it's done (also used by other buttons)
//   "claude-status" event    fired on document with {signed_in, installed} whenever the status is known
(function () {
  let state = { installed: null, signed_in: null }, waiting = false, poll = null;
  const btn = () => document.getElementById("claudeAcct");
  const say = (msg, bad) => (typeof window.toast === "function" ? window.toast(msg, bad) : alert(msg));

  function paint() {
    const b = btn();
    if (!b) return;
    b.disabled = waiting;
    b.classList.toggle("acct-on", state.signed_in === true);
    b.classList.toggle("acct-off", state.signed_in !== true);
    if (waiting) { b.textContent = "⏳ Approve in your browser…"; b.title = "Finish signing in on the Claude page that opened, then come back"; }
    else if (state.installed === false) { b.textContent = "🔑 Claude not installed"; b.title = "Claude Code isn't installed on this computer"; }
    else if (state.signed_in === true) { b.textContent = "✓ Claude connected"; b.title = "Your Claude account is connected. Click to sign in again (if Claude stops answering, or to switch accounts)"; }
    else { b.textContent = "🔑 Connect Claude"; b.title = "Connect your Claude account so you can tell it what to change right inside the editor"; }
  }
  function publish(st) {
    state = { installed: st.installed, signed_in: st.signed_in };
    paint();
    document.dispatchEvent(new CustomEvent("claude-status", { detail: state }));
  }
  async function check() {
    try { publish(await (await fetch("/api/claude-status")).json()); } catch { /* older editor: leave it */ }
    return state;
  }

  window.connectClaude = async function () {
    if (waiting) return say("Finish signing in on the Claude page in your browser, then come back here.");
    let r;
    try { r = await (await fetch("/api/claude-login", { method: "POST", body: "{}" })).json(); }
    catch { return say("Close the editor and open it again from the desktop icon, then try again.", true); }
    if (!r.ok) return say(r.error || "Couldn't open the Claude sign-in.", true);
    waiting = true; paint();
    say("A Claude sign-in page is opening in your browser. Sign in if asked, click Authorize, then come back here.");
    clearInterval(poll);
    const t0 = Date.now();
    poll = setInterval(async () => {
      let st = {};
      try { st = await (await fetch("/api/claude-status")).json(); } catch {}
      // A fresh sign-in shows up as "connected", or the sign-in window closing (already signed in before)
      if ((st.signed_in && (st.login_done !== false)) || Date.now() - t0 > 6 * 60000) {
        clearInterval(poll); waiting = false; publish(st);
        if (st.signed_in) say("Claude is connected ✓ You can now tell it what to change inside the editor.");
        else say("Still not connected. Click 🔑 Connect Claude at the top to try again.", true);
      }
    }, 3000);
  };

  document.addEventListener("DOMContentLoaded", () => {
    const b = btn();
    if (!b) return;
    b.onclick = () => {
      if (state.installed === false) return say("Claude Code isn't installed on this computer, so ask in your Claude chat instead.", true);
      if (state.signed_in === true &&
          !confirm("Claude is already connected ✓\n\nSign in again? Do this if Claude stopped answering in the editor, or to use a different Claude account.")) return;
      window.connectClaude();
    };
    paint();
    check();
    setInterval(() => { if (!waiting && !document.hidden) check(); }, 60000);
  });
  window.checkClaude = check;
})();
