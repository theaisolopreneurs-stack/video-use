// Talk instead of type: a small mic button on the editor's text boxes.
// Click, speak, click again; your words land where the cursor is. The recording only goes to the
// editor on this computer, which turns it into text with the same local model as the captions.
(() => {
  let active = null; // { recorder, button }

  const say = (msg) => {
    if (window.toast) window.toast(msg, true);
    else alert(msg);
  };

  function put(field, text) {
    const at = field.selectionStart ?? field.value.length;
    const end = field.selectionEnd ?? at;
    const before = field.value.slice(0, at);
    const sep = before && !/\s$/.test(before) ? " " : "";
    field.setRangeText(sep + text, at, end, "end");
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
    field.focus();
  }

  function look(button, state) {
    button.dataset.state = state;
    button.textContent = { idle: "🎤", rec: "■", busy: "…" }[state];
    const label = { idle: "Speak instead of typing", rec: "Stop and write it down", busy: "Writing down what you said" }[state];
    button.title = label;
    button.setAttribute("aria-label", label);
    button.disabled = state === "busy";
  }

  async function start(field, button) {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      return say("This browser can't record from a microphone. Try Chrome or Edge.");
    }
    if (active) return; // only one at a time
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      return say(e.name === "NotFoundError" ? "No microphone found on this computer."
        : "The browser needs permission to use your microphone. Allow it, then click the mic again.");
    }
    const chunks = [];
    const recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      clearTimeout(limit);
      active = null;
      look(button, "busy");
      try {
        const res = await (await fetch("/api/dictate", { method: "POST", body: new Blob(chunks), signal: AbortSignal.timeout(180000) })).json();
        if (!res.ok) say("Sorry, that didn't work: " + (res.error || "unknown problem"));
        else if (!res.text) say("I didn't catch any words. Try again a little closer to the mic.");
        else if (field.isConnected) put(field, res.text);
        else say("That box closed before I finished. You said: " + res.text);
      } catch {
        say("Sorry, that took too long or the editor stopped answering. Try again.");
      }
      look(button, "idle");
    };
    active = { recorder, button };
    const limit = setTimeout(() => recorder.state === "recording" && recorder.stop(), 5 * 60 * 1000);
    recorder.start();
    look(button, "rec");
  }

  function addMic(field) {
    field.dataset.mic = "1";
    const wrap = document.createElement("span");
    wrap.className = "micwrap" + (field.classList.contains("grow") ? " grow" : "");
    field.parentNode.insertBefore(wrap, field);
    wrap.appendChild(field);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "micbtn";
    look(button, "idle");
    button.onclick = (e) => {
      e.preventDefault();
      if (active?.button === button) active.recorder.stop();
      else if (button.dataset.state === "idle") start(field, button);
    };
    wrap.appendChild(button);
  }

  const SKIP = '[type=number],[type=color],[type=range],[type=checkbox],[type=file],[type=radio]';
  function scan() {
    document.querySelectorAll("input.field:not([data-mic]):not(.no-mic), textarea.field:not([data-mic]):not(.no-mic)")
      .forEach((f) => { if (!f.matches(SKIP)) addMic(f); });
  }
  let pending = false;
  new MutationObserver(() => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; scan(); });
  }).observe(document.documentElement, { childList: true, subtree: true });
  scan();
})();
