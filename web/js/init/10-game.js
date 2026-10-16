/* A choice or an answer as read: a decade comes as its first year (1980). */
function gameFactLabel(kind, value) {
  return kind === "year" && value !== null && value !== undefined ? t("game.decade", { n: value }) : String(value ?? "");
}
let gameOptionsFilled = false;

async function refreshGame() {
  const r = await apiGet("/api/game");
  if (r.ok) paintGame(r.data);
}

function paintGame(g) {
  const lobby = g.state === "none" || g.state === "over";
  document.getElementById("gameLobby").hidden = !lobby;
  document.getElementById("gameStartForm").hidden = !g.owner;
  // The game's settings wait for the game to be over.
  document.getElementById("gameVoiceForm").hidden = !g.owner || !lobby;
  // One hall shown at a time: the games on the phones, or those played aloud.
  const halls = { phones: g.hall || [], oral: g.hall_oral || [] };
  const kindSelect = document.getElementById("gameHallKind");
  const both = halls.phones.length > 0 && halls.oral.length > 0;
  kindSelect.hidden = !both;
  if (!both) kindSelect.value = halls.oral.length ? "oral" : "phones";
  const hall = halls[kindSelect.value] || [];
  document.getElementById("gameHallBox").hidden = !lobby || !hall.length;
  document.getElementById("gameHallResetRow").hidden = !g.owner;
  document.getElementById("gameHall").replaceChildren(...hall.map((h) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = h.name;
    const wins = document.createElement("span");
    wins.className = "game-points";
    wins.textContent = t(h.wins === 1 ? "game.wins_one" : "game.wins", { n: h.wins });
    li.append(name, wins);
    return li;
  }));
  document.getElementById("gameWaiting").hidden = !!g.owner;
  if (g.owner && !gameOptionsFilled && g.round_choices) {
    gameOptionsFilled = true;
    const fill = (id, values, picked) => {
      const select = document.getElementById(id);
      select.innerHTML = "";
      values.forEach((v) => {
        const o = document.createElement("option");
        o.value = String(v);
        o.textContent = String(v);
        o.selected = v === picked;
        select.appendChild(o);
      });
    };
    fill("gameRounds", g.round_choices, 10);
    fill("gameSeconds", g.second_choices, 20);
  }
  if (g.owner) {
    paintGameLists(g.lists || []);
    const forget = document.getElementById("gameForgetBtn");
    forget.textContent = t(g.asked === 1 ? "game.forget_one" : "game.forget_n", { n: g.asked || 0 });
    document.getElementById("gameForgetRow").hidden = !g.asked;
  }

  document.getElementById("gamePlay").hidden = g.state === "none";
  const round = document.getElementById("gameRound");
  const joining = g.state === "joining";
  const role = document.getElementById("gameRole");
  role.hidden = g.mode === "oral"
    || !(joining || ((g.state === "playing" || g.state === "reveal") && g.role !== "player"));
  if (!role.hidden) {
    document.getElementById("gameRoleText").textContent = t(g.role === "player" ? "game.role_is_player"
      : g.role === "spectator" ? "game.role_is_spectator" : "game.role_ask");
    document.getElementById("gamePlayBtn").hidden = g.role === "player";
    document.getElementById("gameWatchBtn").hidden = g.role === "spectator";
    document.getElementById("gameRoleCount").textContent = t("game.role_count",
      { p: g.players || 0, s: g.spectators || 0, u: g.undecided || 0 });
    document.getElementById("gameGoRow").hidden = !(joining && g.owner);
  }
  if (joining) {
    round.textContent = t("game.join_wait", { s: g.join_left || 0 });
  } else if (g.state === "playing") {
    round.textContent = t("game.round", { n: g.round, total: g.rounds }) + " · " + t("game.remaining", { s: g.remaining || 0 });
  } else if (g.state === "thinking") {
    round.textContent = t("game.round", { n: g.round, total: g.rounds }) + " · " + t("game.thinking", { s: g.think_left || 0 });
  } else if (g.state === "reveal") {
    round.textContent = t("game.round", { n: g.round, total: g.rounds });
  } else if (g.state === "over") {
    round.textContent = t("game.over");
  }

  const podium = document.getElementById("gamePodium");
  const places = g.state === "over" ? (g.podium || []) : [];
  podium.hidden = !places.length;
  podium.replaceChildren(...places.map((place, i) => {
    const li = document.createElement("li");
    li.className = "podium-place";
    li.dataset.place = String(i + 1);
    li.setAttribute("aria-label", t("game.place", { n: i + 1 }));
    const names = document.createElement("span");
    names.className = "podium-names";
    names.textContent = place.names.join(", ");
    const points = document.createElement("span");
    points.className = "podium-points";
    points.textContent = t(place.points === 1 ? "game.points_one" : "game.points", { n: place.points });
    const step = document.createElement("span");
    step.className = "podium-step";
    step.setAttribute("aria-hidden", "true");
    step.textContent = String(i + 1);
    if (i === 0) {
      const crown = document.createElement("span");
      crown.className = "podium-crown";
      crown.dataset.icon = "crown";
      crown.setAttribute("aria-hidden", "true");
      li.append(crown);
    }
    li.append(names, points, step);
    return li;
  }));

  const asking = ["playing", "thinking", "reveal"].includes(g.state) && g.kind;
  const ask = document.getElementById("gameAsk");
  ask.hidden = !(asking && (g.kinds || []).join() !== "song");
  ask.textContent = ask.hidden ? "" : t("game.ask_" + g.kind);
  const hint = document.getElementById("gameHintText");
  hint.hidden = !(g.state === "playing" && g.hint);
  hint.textContent = hint.hidden ? "" : g.hint.letter ? t("game.hint_letter", { l: g.hint.letter })
    : t(g.hint.era === "before" ? "game.hint_before" : "game.hint_after");

  const box = document.getElementById("gameChoices");
  const signature = JSON.stringify([g.kind, g.choices || []]);
  if (signature !== gameChoicesSignature) {
    gameChoicesSignature = signature;
    box.innerHTML = "";
    (g.choices || []).forEach((label, index) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn game-choice";
      btn.textContent = gameFactLabel(g.kind, label);
      btn.addEventListener("click", async () => {
        const r = await apiPost("/api/game/answer", { choice: index });
        if (!r.ok) showError(r.error, t("game.title"));
        else paintGame(Object.assign({}, g, r.data, { owner: g.owner }));
      });
      box.appendChild(btn);
    });
  }
  [...box.children].forEach((btn, index) => {
    btn.disabled = g.state !== "playing" || g.mine !== null || g.role !== "player";
    btn.classList.toggle("is-mine", g.mine === index);
    btn.classList.toggle("is-right", g.answer === index && g.state !== "playing");
    btn.classList.toggle("is-wrong", g.mine === index && g.answer !== undefined && g.answer !== index
                                     && g.state !== "playing");
  });

  const result = document.getElementById("gameResult");
  let text = "";
  if (g.state === "playing") {
    text = g.mine !== null ? t("game.answered", { n: g.answered }) : "";
  } else if (g.state === "reveal" || (g.state === "over" && g.answer !== undefined)) {
    if (g.gain >= 2) text = t("game.fastest_you");
    else if (g.gain === 1) text = t("game.right");
    else if (g.mine !== null) text = t("game.wrong");
    else text = t("game.no_answer");
    if (g.fastest && g.gain < 2) text += " " + t("game.fastest", { name: g.fastest });
  }
  const revealed = g.answer_label && (g.state === "reveal" || g.state === "over");
  const fact = revealed && g.kind && g.kind !== "song" ? gameFactLabel(g.kind, g.answer_fact) : "";
  if (g.mode === "oral") {
    text = g.state === "playing" ? t("game.oral_listen") : g.state === "thinking" ? t("game.oral_think")
      : revealed ? (fact ? fact + " · " : "") + g.answer_label : "";
  }
  if (g.state === "over" && g.error) text = errorLabel(g.error);
  result.textContent = text;
  // On the phones the right choice lights up; with another question than the song, the song is named too.
  const song = document.getElementById("gameSong");
  song.hidden = !(g.mode !== "oral" && fact);
  song.textContent = song.hidden ? "" : t("game.it_was", { song: g.answer_label });

  // Aloud: the host moves the game on, and marks who found it (the first one marked scores twice).
  const host = g.owner && g.mode === "oral";
  const marks = document.getElementById("gameMarks");
  marks.hidden = !(host && g.state === "reveal" && (g.oral_names || []).length);
  if (!marks.hidden) {
    marks.replaceChildren(...g.oral_names.map((one) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-small game-mark";
      btn.setAttribute("aria-pressed", one.marked ? "true" : "false");
      btn.append(one.name);
      if (one.marked) {
        const gain = document.createElement("span");
        gain.className = "game-mark-gain";
        gain.textContent = one.first ? "+2" : "+1";
        btn.append(gain);
      }
      btn.addEventListener("click", async () => {
        const r = await apiPost("/api/game/mark", { name: one.name, on: !one.marked });
        if (!r.ok) showError(r.error, t("game.title"));
        else paintGame(Object.assign({}, g, r.data, { owner: g.owner, hall: g.hall, hall_oral: g.hall_oral }));
      });
      return btn;
    }));
  }
  const atPace = host && g.pace === "host";
  document.getElementById("gameHostRow").hidden = !(atPace && ["playing", "thinking", "reveal"].includes(g.state));
  document.getElementById("gameCutBtn").hidden = g.state !== "playing";
  const next = document.getElementById("gameNextBtn");
  next.hidden = !["thinking", "reveal"].includes(g.state);
  next.dataset.i18n = g.state === "thinking" ? "game.answer_now" : "game.next";
  next.textContent = t(next.dataset.i18n);

  document.getElementById("btnGameStop").hidden = !(g.owner && ["joining", "playing", "thinking", "reveal"].includes(g.state));

  const scores = document.getElementById("gameScores");
  scores.innerHTML = "";
  (g.scores || []).forEach((s) => {
    const li = document.createElement("li");
    li.classList.toggle("is-me", !!s.me);
    const name = document.createElement("span");
    name.textContent = s.name;
    if (s.wins) {
      const wins = document.createElement("span");
      wins.className = "game-wins";
      wins.textContent = t(s.wins === 1 ? "game.wins_one" : "game.wins", { n: s.wins });
      name.append(wins);
    }
    const points = document.createElement("span");
    points.className = "game-points";
    points.textContent = t(s.points === 1 ? "game.points_one" : "game.points", { n: s.points });
    li.append(name, points);
    scores.appendChild(li);
  });
}

/* The lists a game can ask about; the choice stays while the lists change, and on this device. */
const GAME_LIST_KEY = "rukebox_game_list";

function paintGameLists(lists) {
  const select = document.getElementById("gameList");
  const shown = [...select.options].slice(1).map((o) => o.value + "\n" + o.textContent).join("|");
  if (shown !== lists.map((l) => l.id + "\n" + l.name).join("|")) {
    let picked = select.value;
    if (!select.dataset.ready) {
      select.dataset.ready = "1";
      try { picked = localStorage.getItem(GAME_LIST_KEY) || ""; } catch (e) { /* private window */ }
    }
    select.replaceChildren(select.options[0], ...lists.map((l) => {
      const o = document.createElement("option");
      o.value = l.id;
      o.textContent = l.name;
      return o;
    }));
    select.value = lists.some((l) => l.id === picked) ? picked : "";
  }
  document.getElementById("gameListRow").hidden = !lists.length;
}

document.getElementById("gameList").addEventListener("change", (e) => {
  try { localStorage.setItem(GAME_LIST_KEY, e.target.value); } catch (err) { /* private window */ }
});

document.getElementById("gameStartForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const oral = document.getElementById("gameMode").value === "oral";
  const r = await apiPost("/api/game/start", {
    list: document.getElementById("gameList").value,
    kinds: gameKinds(),
    hint: !oral && document.getElementById("gameHint").checked,
    remember: document.getElementById("gameRemember").checked,
    rounds: Number(document.getElementById("gameRounds").value),
    seconds: Number(document.getElementById("gameSeconds").value),
    mode: oral ? "oral" : "phones",
    pace: oral ? document.getElementById("gamePace").value : "auto",
    names: oral ? gameNamesWith(document.getElementById("gameNamesNew")) : [],
  });
  if (!r.ok) showError(r.error, t("game.title"));
  refreshGame();
});

document.getElementById("gameCoverGo").addEventListener("click", () => setActiveView("home", "game"));

/* The first names of a game aloud, kept on this device from one game to the next. */
const GAME_NAMES_KEY = "rukebox_game_names";
const GAME_NAMES_MAX = 12;

function gameNames() {
  try {
    const kept = JSON.parse(localStorage.getItem(GAME_NAMES_KEY) || "[]");
    return Array.isArray(kept) ? kept.filter((n) => typeof n === "string" && n) : [];
  } catch (e) {
    return [];
  }
}

function saveGameNames(names) {
  try {
    localStorage.setItem(GAME_NAMES_KEY, JSON.stringify(names));
  } catch (e) {
    // Private browsing: the names last as long as the page.
  }
  gameNames.held = names;
}

function heldGameNames() {
  return gameNames.held || gameNames();
}

function addGameName(input) {
  const name = input.value.trim().slice(0, 24);
  if (!name) return;
  const names = heldGameNames();
  input.value = "";
  if (names.some((n) => n.toLowerCase() === name.toLowerCase())) {
    showToast(t("game.name_exists", { name }));
    return;
  }
  if (names.length >= GAME_NAMES_MAX) {
    showToast(t("game.names_full", { n: GAME_NAMES_MAX }), "", { error: true });
    return;
  }
  saveGameNames(names.concat(name));
  showToast(t("game.name_added", { name }));
  input.focus();
}

/* The names to send: a name typed and not added yet counts too. */
function gameNamesWith(input) {
  if (input && input.value.trim()) addGameName(input);
  return heldGameNames();
}

function gameNamesList() {
  const names = heldGameNames();
  const list = document.createElement("div");
  list.className = "duration-list";
  if (!names.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = t("game.names_none");
    list.appendChild(empty);
    return list;
  }
  names.forEach((name) => {
    const row = document.createElement("div");
    row.className = "duration-row";
    const label = document.createElement("span");
    label.textContent = name;
    const off = document.createElement("button");
    off.type = "button";
    off.className = "btn btn-icon duration-row-x btn-danger-outline";
    off.dataset.icon = "trash";
    off.title = t("game.name_remove", { name });
    off.setAttribute("aria-label", off.title);
    off.addEventListener("click", () => {
      saveGameNames(heldGameNames().filter((one) => one !== name));
      list.replaceWith(gameNamesList());
    });
    row.append(label, off);
    list.appendChild(row);
  });
  return list;
}

(function gameNamesField() {
  const el = document.getElementById("gameNames");
  const input = document.createElement("input");
  input.type = "text";
  input.className = "text-input duration-new game-name-new";
  input.id = "gameNamesNew";
  input.maxLength = 24;
  input.placeholder = "Ana";
  input.autocomplete = "off";
  const plus = document.createElement("button");
  plus.type = "button";
  plus.className = "btn btn-icon duration-add-btn";
  plus.dataset.icon = "plus";
  plus.title = t("common.add");
  plus.setAttribute("aria-label", t("common.add"));
  plus.addEventListener("click", () => addGameName(input));
  const list = document.createElement("button");
  list.type = "button";
  list.className = "btn btn-icon duration-open";
  list.dataset.icon = "list";
  list.title = t("game.names_manage");
  list.setAttribute("aria-label", list.title);
  list.addEventListener("click", () => openModal({ title: t("game.names"), bodyNode: gameNamesList(), actions: false }));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();  // otherwise the game starts
      addGameName(input);
    }
  });
  el.replaceChildren(input, plus, list);
}());

function paintGameMode() {
  const oral = document.getElementById("gameMode").value === "oral";
  document.getElementById("gamePaceRow").hidden = !oral;
  document.getElementById("gameNamesRow").hidden = !oral;
  // The hint shows on the phones: aloud, nobody looks at one.
  document.getElementById("gameHintRow").hidden = oral;
}
document.getElementById("gameMode").addEventListener("change", paintGameMode);

/* The questions, the hint and the memory of a game, kept on this device from one game to the next. */
const GAME_OPTIONS_KEY = "rukebox_game_options";

function gameKinds() {
  return codecValues(document.getElementById("gameKinds"));
}

function saveGameOptions() {
  try {
    localStorage.setItem(GAME_OPTIONS_KEY, JSON.stringify({ kinds: gameKinds(),
      hint: document.getElementById("gameHint").checked, remember: document.getElementById("gameRemember").checked }));
  } catch (e) { /* private window */ }
}

(function restoreGameOptions() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(GAME_OPTIONS_KEY) || "null"); } catch (e) { /* private window */ }
  const kinds = document.getElementById("gameKinds");
  const known = (saved && Array.isArray(saved.kinds) ? saved.kinds : []).filter((kind) => GAME_KINDS.includes(kind));
  kinds.value = (known.length ? GAME_KINDS.filter((kind) => known.includes(kind)) : ["song"]).join(",");
  paintCodecField(kinds);
  if (saved) {
    document.getElementById("gameHint").checked = !!saved.hint;
    document.getElementById("gameRemember").checked = !!saved.remember;
  }
})();

document.getElementById("gameKinds").addEventListener("change", saveGameOptions);
window.LANG_CHANGE_LISTENERS.push(() => paintCodecField(document.getElementById("gameKinds")));
["gameHint", "gameRemember"].forEach((id) => document.getElementById(id).addEventListener("change", saveGameOptions));

document.getElementById("gameForgetBtn").addEventListener("click", async () => {
  const sure = await showConfirm(t("game.forget_confirm"), t("game.forget"));
  if (!sure) return;
  const r = await apiDelete("/api/game/asked");
  if (!r.ok) showToolError(t("common.failed"), r);
  else showToast(t("game.forgotten"));
  refreshGame();
});
paintGameMode();
document.getElementById("gameHallKind").addEventListener("change", refreshGame);
document.getElementById("gameCutBtn").addEventListener("click", async () => {
  await apiPost("/api/game/cut", {});
  refreshGame();
});
document.getElementById("gameNextBtn").addEventListener("click", async () => {
  await apiPost(document.getElementById("gameNextBtn").dataset.i18n === "game.answer_now" ? "/api/game/cut"
    : "/api/game/next", {});
  refreshGame();
});

[["gamePlayBtn", true], ["gameWatchBtn", false]].forEach(([id, play]) => {
  document.getElementById(id).addEventListener("click", async () => {
    const r = await apiPost("/api/game/join", { play });
    if (!r.ok) showError(r.error, t("game.title"));
    refreshGame();
  });
});
document.getElementById("gameHallReset").addEventListener("click", async () => {
  const sure = await showConfirm(t("game.hall_reset_confirm"), t("game.hall_reset"));
  if (!sure) return;
  const r = await apiDelete("/api/game/hall" + (document.getElementById("gameHallKind").value === "oral" ? "?kind=oral" : ""));
  if (!r.ok) showToolError(t("common.failed"), r);
  else showToast(t("game.hall_reset_done"));
  refreshGame();
});
document.getElementById("gameGoBtn").addEventListener("click", async () => {
  await apiPost("/api/game/go", {});
  refreshGame();
});

document.getElementById("btnGameStop").addEventListener("click", async () => {
  await apiPost("/api/game/stop", {});
  refreshGame();
});

refreshEvery(refreshGame, 1000, ["home/game"]);

async function refreshRecap() {
  const period = document.getElementById("recapPeriod").value;
  const r = await apiGet("/api/journal/recap?period=" + encodeURIComponent(period));
  if (!r.ok || !r.data || r.data.enabled === false) return;
  const d = r.data;
  const kpis = document.getElementById("recapKpis");
  kpis.innerHTML = "";
  kpis.append(
    kpiTile("headphones", t("recap.listening"), formatDuration(d.seconds_music), null, { hero: true }),
    kpiTile("music", t("recap.songs"), String(Math.round(d.tracks_played || 0))),
    kpiTile("calendar", t("recap.days"), String(d.days || 0)),
    kpiTile("users", t("recap.artists"), String(d.artists || 0)),
    kpiTile("heart", t("recap.liked"), String(d.likes || 0)),
  );
  const list = (id, rows, text, count, link) => {
    const box = document.getElementById(id);
    box.innerHTML = "";
    rows.forEach((row) => {
      const li = document.createElement("li");
      const filter = link ? link(row) : null;
      const name = filter ? libraryLink(text(row), filter) : document.createElement("span");
      if (!filter) name.textContent = text(row);
      const n = document.createElement("span");
      n.className = "recap-count";
      const plays = Math.round(row.count);
      n.textContent = count ? count(row) : t(plays === 1 ? "recap.plays_one" : "recap.plays", { n: plays });
      li.append(name, n);
      box.appendChild(li);
    });
    box.previousElementSibling.hidden = rows.length === 0;
  };
  const dayName = (day) => new Date(day + "T12:00:00").toLocaleDateString(currentLang,
    { weekday: "long", day: "numeric", month: "long" });
  list("recapBestDay", d.best_day ? [d.best_day] : [], (b) => dayName(b.day), (b) => formatDuration(b.seconds));
  const song = (s) => (s.in_library && s.name ? { q: s.name } : null);
  list("recapMorning", d.morning ? [d.morning] : [], (m) => (m.artist ? m.title + " - " + m.artist : m.title),
       (m) => t(Math.round(m.count) === 1 ? "recap.times_one" : "recap.times", { n: Math.round(m.count) }), song);
  list("recapArtists", d.top_artists || [], (a) => a.artist, null, (a) => ({ artist: a.artist }));
  list("recapTracks", d.top_tracks || [], (s) => s.artist ? s.title + " - " + s.artist : s.title, null, song);
  document.getElementById("recapEmpty").hidden = (d.tracks_played || 0) > 0;
}
document.getElementById("recapPeriod").addEventListener("change", refreshRecap);
refreshEvery(refreshRecap, 300000, ["stats/recap"]);

let lastCardSeen = null;

