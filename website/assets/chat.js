(() => {
  const API_BASE = "";
  const storageKey = "solright_chat_v4_savings";

  const root = document.getElementById("sr-chat");
  const panel = document.getElementById("sr-panel");
  const toggle = document.getElementById("sr-toggle");
  const closeBtn = document.getElementById("sr-close");
  const messages = document.getElementById("sr-messages");
  const form = document.getElementById("sr-form");
  const input = document.getElementById("sr-input");
  const openHero = document.getElementById("openChatHero");
  const openEstimate = document.getElementById("openChatEstimate");

  let state = loadState();
  let isOpen = false;
  let welcomeLoading = false;
  let hasResults = false;

  function loadState() {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return { session_id: null, web_id: null, started: false };
      return JSON.parse(raw);
    } catch {
      return { session_id: null, web_id: null, started: false };
    }
  }

  function saveState() {
    localStorage.setItem(storageKey, JSON.stringify(state));
  }

  function addBubble(text, who = "bot") {
    const el = document.createElement("div");
    el.className = `bubble ${who}`;
    if (who === "bot") {
      el.innerHTML = formatBotHtml(text);
    } else {
      el.textContent = text;
    }
    messages.appendChild(el);
    messages.scrollTop = messages.scrollHeight;
    return el;
  }

  function formatBotHtml(text) {
    const esc = String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    return esc
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/^### (.+)$/gm, '<div class="sr-h3">$1</div>')
      .replace(/^## (.+)$/gm, '<div class="sr-h2">$1</div>')
      .replace(/^- (.+)$/gm, "• $1")
      .replace(/\n/g, "<br>");
  }

  function fmt(n, digits = 0) {
    if (n === null || n === undefined || n === "") return "—";
    const num = Number(n);
    if (Number.isNaN(num)) return String(n);
    return num.toLocaleString(undefined, {
      maximumFractionDigits: digits,
      minimumFractionDigits: digits > 0 ? Math.min(digits, 1) : 0,
    });
  }

  function sunBar(seg) {
    const score = Math.min(100, Math.round(((seg.sunshineMedianHours || 0) / 1450) * 100));
    const wrap = document.createElement("div");
    wrap.className = "sr-sunrow";
    wrap.innerHTML = `
      <div class="sr-sunrow-top">
        <strong>${seg.recommendedPanelsOnSegment || 0} panels · ${seg.direction || "?"} face</strong>
        <span>${seg.quality || ""} · pitch ${seg.pitchDegrees ?? "—"}°</span>
      </div>
      <div class="sr-suntrack"><div class="sr-sunfill q-${seg.quality || "fair"}" style="width:${score}%"></div></div>
    `;
    return wrap;
  }

  function addEstimateCard(mediaItems, estimate) {
    if (!estimate) return;

    hasResults = true;
    root.classList.add("has-results");

    const card = document.createElement("div");
    card.className = "sr-card sr-card-results";

    const title = document.createElement("div");
    title.className = "sr-card-title";
    title.innerHTML = `<strong>Your solar savings snapshot</strong><span>${estimate?.address || ""}</span>`;
    card.appendChild(title);

    const conf = estimate.quoteConfidence != null ? `${fmt(estimate.quoteConfidence)}% confidence` : "";
    const offset =
      estimate.targetOffsetPct != null ? `${fmt(estimate.targetOffsetPct)}% target offset` : "usage-sized";
    const stats = document.createElement("div");
    stats.className = "sr-stats";
    stats.innerHTML = `
        <div class="hi"><span>Recommended system</span><strong>${fmt(estimate.recommendedPanels)} panels</strong><em>${fmt(estimate.systemSizeKw, 1)} kW DC · ${offset}</em></div>
        <div><span>Est. yearly production</span><strong>${fmt(estimate.yearlyEnergyDcKwh)} kWh</strong><em>~${fmt(estimate.monthlyEnergyKwh)} kWh/mo</em></div>
        <div><span>Your usage basis</span><strong>${estimate.monthlyBillUsd != null ? "$" + fmt(estimate.monthlyBillUsd) + "/mo bill" : "kWh input"}</strong><em>~${fmt(estimate.estimatedMonthlyUsageKwh)} kWh/mo</em></div>
        <div><span>Roof capacity (not the plan)</span><strong>${fmt(estimate.maxPanels)} panels max</strong><em>${conf || "physical limit only"}</em></div>
      `;
    card.appendChild(stats);

    // Savings highlight block
    if (estimate.estimatedMonthlySavingsUsd != null) {
      const sav = document.createElement("div");
      sav.className = "sr-savings";
      sav.innerHTML = `
        <div class="sr-subhead">How this lowers your electric bill</div>
        <p class="sr-save-explain">Your panels produce power your home uses first — so you buy fewer kWh from the utility and the energy portion of your bill drops.</p>
        <div class="sr-save-grid">
          <div class="hi"><span>Est. monthly savings</span><strong>$${fmt(estimate.estimatedMonthlySavingsUsd, 0)}</strong><em>~${fmt(estimate.estimatedBillReductionPct)}% of current bill</em></div>
          <div><span>New bill ballpark</span><strong>$${fmt(estimate.estimatedNewMonthlyBillUsd, 0)}/mo</strong><em>before fixed utility fees</em></div>
          <div><span>Est. yearly savings</span><strong>$${fmt(estimate.estimatedYearlySavingsUsd, 0)}</strong><em>planning estimate</em></div>
          <div><span>10-year ballpark</span><strong>$${fmt(estimate.estimated10YearSavingsUsd, 0)}</strong><em>20-yr ~$${fmt(estimate.estimated20YearSavingsUsd, 0)}</em></div>
        </div>
        <div class="sr-cta-box">Any more questions? Share your <strong>name + phone</strong>, then reply <strong>Yes</strong> to: “Yes, you can call me with an AI agent to discuss my solar savings estimate and an estimated installation cost.” We’ll start the AI call right away (you can opt out anytime).</div>
      `;
      card.appendChild(sav);
    }

    const note = document.createElement("div");
    note.className = "sr-card-note";
    note.textContent =
      estimate.savingsAssumptions ||
      "Planning estimate only. Final pricing depends on incentives, utility rules, financing, and a site survey.";
    card.appendChild(note);

    messages.appendChild(card);
    messages.scrollTop = messages.scrollHeight;
  }

  function setOpen(open) {
    isOpen = !!open;
    root.classList.toggle("is-open", isOpen);
    toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    panel.setAttribute("aria-hidden", isOpen ? "false" : "true");
    if (hasResults) root.classList.add("has-results");

    if (isOpen) {
      window.setTimeout(() => input && input.focus(), 280);
      if (!state.started && !welcomeLoading) bootstrapWelcome();
    }
  }

  async function bootstrapWelcome() {
    welcomeLoading = true;
    state.started = true;
    saveState();
    addBubble("Connecting…", "sys");
    try {
      const res = await fetch(`${API_BASE}/api/welcome`);
      const data = await res.json();
      const last = messages.querySelector(".bubble.sys:last-child");
      if (last) last.remove();
      addBubble(
        data.reply ||
          "Hi, I'm Dave with SOL-RIGHT Solar. Share your address for a solar quote, or ask a question.",
        "bot"
      );
    } catch (err) {
      const last = messages.querySelector(".bubble.sys:last-child");
      if (last) last.remove();
      addBubble(
        "Hi, I'm Dave with SOL-RIGHT Solar. Share your address for a solar quote, or ask a question.",
        "bot"
      );
    } finally {
      welcomeLoading = false;
    }
  }

  async function sendMessage(text) {
    addBubble(text, "user");
    const btn = form.querySelector("button");
    btn.disabled = true;
    input.value = "";
    addBubble("Building your quote visuals…", "sys");
    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          session_id: state.session_id,
          web_id: state.web_id,
        }),
      });
      const data = await res.json().catch(() => ({}));
      const thinking = messages.querySelector(".bubble.sys:last-child");
      if (thinking) thinking.remove();
      if (!res.ok) {
        addBubble(data.detail || "Sorry, something went wrong. Please try again.", "bot");
        return;
      }
      state.session_id = data.session_id;
      state.web_id = data.web_id;
      saveState();
      addBubble(data.reply || "(empty reply)", "bot");
      if ((data.media && data.media.length) || data.estimate) {
        addEstimateCard(data.media || [], data.estimate || null);
      }
    } catch (err) {
      const thinking = messages.querySelector(".bubble.sys:last-child");
      if (thinking) thinking.remove();
      addBubble("Network error talking to the assistant. Is the API running?", "bot");
    } finally {
      btn.disabled = false;
      input.focus();
    }
  }

  setOpen(false);
  toggle.addEventListener("click", () => setOpen(!isOpen));
  closeBtn.addEventListener("click", () => setOpen(false));
  openHero && openHero.addEventListener("click", () => setOpen(true));
  openEstimate && openEstimate.addEventListener("click", () => setOpen(true));

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = (input.value || "").trim();
    if (!text) return;
    if (!isOpen) setOpen(true);
    if (!state.started && !welcomeLoading) {
      state.started = true;
      saveState();
    }
    sendMessage(text);
  });
})();
