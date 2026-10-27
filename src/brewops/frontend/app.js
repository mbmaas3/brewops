// BrewOps frontend — vanilla JS, no dependencies, no build step.

async function fetchJSON(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

// ---- dashboard ----

// Module-level state for date filtering
const filter = { from: "", to: "" };
let loadId = 0;

function filterQuery() {
  if (!filter.from && !filter.to) {
    return "";
  }
  const params = new URLSearchParams();
  if (filter.from) params.set("date_from", filter.from);
  if (filter.to) params.set("date_to", filter.to);
  return "?" + params.toString();
}

function setFilterMessage(text) {
  const message = document.getElementById("filter-message");
  message.textContent = text;
  message.className = text ? "message error" : "message";
}

function renderDrinkBars(perDrink) {
  const container = document.getElementById("drink-bars");
  container.innerHTML = "";
  const max = Math.max(1, ...perDrink.map((d) => d.count));
  for (const drink of perDrink) {
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = `
      <span class="bar-label">${drink.label}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${(drink.count / max) * 100}%"></span></span>
      <span class="bar-count">${drink.count}</span>`;
    container.appendChild(row);
  }
}

function renderTimeline(perDay) {
  const svg = document.getElementById("timeline");
  const emptyNote = document.getElementById("timeline-empty");
  const empty = perDay.length === 0;
  svg.hidden = empty;
  emptyNote.hidden = !empty;
  svg.innerHTML = "";
  if (empty) return;
  
  const width = 600;
  const height = 130;
  const max = Math.max(...perDay.map((d) => d.count));
  const barWidth = width / perDay.length;
  perDay.forEach((day, i) => {
    const barHeight = (day.count / max) * (height - 10);
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", i * barWidth);
    rect.setAttribute("y", height - barHeight);
    rect.setAttribute("width", Math.max(0.5, barWidth - 0.6));
    rect.setAttribute("height", barHeight);
    rect.setAttribute("class", "timeline-bar");
    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = `${day.day}: ${day.count} pours`;
    rect.appendChild(title);
    svg.appendChild(rect);
  });
}

function escapeHTML(text) {
  const el = document.createElement("span");
  el.textContent = text;
  return el.innerHTML;
}

function renderTapCards(healths, filtered = false) {
  const container = document.getElementById("tap-cards");
  container.innerHTML = "";
  for (const m of healths) {
    const card = document.createElement("div");
    card.className = "card";
    const maintenance = m.last_maintenance
      ? `${m.last_maintenance.type} on ${m.last_maintenance.timestamp.slice(0, 10)}`
      : "none on record";
    const errors = m.recent_errors.length
      ? `<p class="errors">Recent errors: ${m.recent_errors
          .map((e) => `${e.error_code || "?"} (${e.timestamp.slice(0, 10)})`)
          .join(", ")}</p>`
      : "";
    const specialty = m.specialty
      ? `<p class="specialty">Specialty: ${escapeHTML(m.specialty.label)} (${m.specialty.count} pours)</p>`
      : "";
    const pourText = `${m.brew_count} pours${filtered ? " in range" : ""}`;
    card.innerHTML = `
      <h3>${m.name}</h3>
      <p class="badge">${m.has_telemetry ? "telemetry" : "manual log"}</p>
      <p>${pourText} · last ${m.last_brew ? m.last_brew.slice(0, 16) : "never"}</p>
      ${specialty}
      <p>Last maintenance: ${maintenance}</p>
      ${errors}`;
    container.appendChild(card);
  }
}

async function loadDashboard() {
  // Validate date range
  if (filter.from && filter.to && filter.from > filter.to) {
    setFilterMessage("From date is after To date.");
    return;
  }

  const myLoad = ++loadId;
  const query = filterQuery();
  
  try {
    // Fetch stats
    const stats = await fetchJSON("/api/stats" + query);
    if (myLoad !== loadId) return;
    
    // Update total label based on filter
    const totalLabel = document.getElementById("total-label");
    totalLabel.textContent = filter.from || filter.to ? "pours in range" : "pours total";
    
    // Update dashboard numbers
    document.getElementById("total-brews").textContent = stats.total_brews;
    const lastDay = stats.per_day[stats.per_day.length - 1];
    document.getElementById("brews-today").textContent = lastDay ? lastDay.count : 0;
    renderDrinkBars(stats.per_drink);
    renderTimeline(stats.per_day);
    
    // Fetch taps list (unfiltered)
    const taps = await fetchJSON("/api/taps");
    if (myLoad !== loadId) return;
    document.getElementById("tap-count").textContent = taps.length;
    
    // Fetch tap health for each tap (filtered)
    const healths = await Promise.all(
      taps.map((t) => fetchJSON(`/api/taps/${t.id}` + query))
    );
    if (myLoad !== loadId) return;
    
    renderTapCards(healths, Boolean(filter.from || filter.to));
    setFilterMessage("");
  } catch (error) {
    if (filter.from || filter.to) {
      setFilterMessage(error.message);
      return;
    }
    throw error;
  }
}

// ---- forms ----

function setupFilter() {
  const fromInput = document.getElementById("filter-from");
  const toInput = document.getElementById("filter-to");
  const clearButton = document.getElementById("filter-clear");
  
  fromInput.addEventListener("change", () => {
    filter.from = fromInput.value;
    loadDashboard().catch(console.error);
  });
  
  toInput.addEventListener("change", () => {
    filter.to = toInput.value;
    loadDashboard().catch(console.error);
  });
  
  clearButton.addEventListener("click", () => {
    fromInput.value = "";
    toInput.value = "";
    filter.from = "";
    filter.to = "";
    loadDashboard().catch(console.error);
  });
}

function localNow() {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16); // datetime-local format
}

function fillSelect(select, items, valueKey, labelKey) {
  select.innerHTML = "";
  for (const item of items) {
    const option = document.createElement("option");
    option.value = item[valueKey];
    option.textContent = item[labelKey];
    select.appendChild(option);
  }
}

async function setupForms() {
  const taps = await fetchJSON("/api/taps");
  const drinks = await fetchJSON("/api/drink-types");
  fillSelect(document.getElementById("brew-tap"), taps, "id", "name");
  fillSelect(document.getElementById("brew-drink"), drinks, "name", "label");
  fillSelect(document.getElementById("maintenance-tap"), taps, "id", "name");
  document.getElementById("brew-timestamp").value = localNow();
  document.getElementById("maintenance-timestamp").value = localNow();

  document.getElementById("brew-form").addEventListener("submit", (event) =>
    submitForm(event, "/api/brews", "brew-message", () => ({
      tap_id: Number(document.getElementById("brew-tap").value),
      drink_type: document.getElementById("brew-drink").value,
      timestamp: document.getElementById("brew-timestamp").value,
    }))
  );

  document.getElementById("maintenance-form").addEventListener("submit", (event) =>
    submitForm(event, "/api/maintenance", "maintenance-message", () => ({
      tap_id: Number(document.getElementById("maintenance-tap").value),
      type: document.getElementById("maintenance-type").value,
      timestamp: document.getElementById("maintenance-timestamp").value,
      note: document.getElementById("maintenance-note").value || null,
    }))
  );
}

async function submitForm(event, url, messageId, buildPayload) {
  event.preventDefault();
  const message = document.getElementById(messageId);
  message.textContent = "";
  message.className = "message";
  try {
    await fetchJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPayload()),
    });
    message.textContent = "Logged.";
    message.classList.add("ok");
    await loadDashboard();
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  }
}

loadDashboard().catch((error) => {
  document.getElementById("total-brews").textContent = "!";
  console.error("Dashboard failed to load:", error);
});
setupFilter();
setupForms().catch((error) => console.error("Form setup failed:", error));
