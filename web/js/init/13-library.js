function libraryButton(item, next) {
  const b = document.createElement("button");
  b.type = "button";
  const name = item.title || item.name || "";
  if (next) {
    b.className = "btn btn-small library-next";
    b.dataset.icon = "list";
    b.title = t("library.next_aria", { title: name });
    const label = document.createElement("span");
    label.textContent = t("library.next");
    b.append(label);
    b.setAttribute("aria-label", t("library.next_aria", { title: name }));
  } else {
    b.className = "btn btn-icon btn-small recent-play";
    b.dataset.icon = "play";
    b.setAttribute("aria-label", t("library.play", { title: name }));
    b.title = t("library.play", { title: name });
  }
  b.dataset.costAction = next ? "queue" : "play_now";
  paintCost(b);
  b.addEventListener("click", async () => {
    const body = { key: item.key };
    let voice = null;
    if (next && dedicationsOn) {
      const dedication = await askDedication();
      if (dedication === null) return;
      if (dedication.message) body.message = dedication.message;
      voice = dedication.voice;
    }
    b.disabled = true;
    if (voice) {
      const sent = await apiFetch("/api/dedications/voice", {
        method: "POST", headers: { "Content-Type": voice.type || "audio/webm" }, body: voice,
      });
      if (!sent.ok) {
        b.disabled = false;
        showToolError(t("dedication.voice_failed"), sent);
        return;
      }
      body.voice = sent.data.id;
    }
    const r = await apiPost(next ? "/api/library/queue" : "/api/library/play", body);
    b.disabled = false;
    if (!r.ok) {
      showToolError(t("home.transport_failed"), r);
      return;
    }
    if (next) {
      const started = r.data && r.data.started;
      showToast(started ? t("library.started", { title: name })
        : t("library.queued", { title: name }),
      started ? "" : t("library.position", { n: (r.data && r.data.position) || 1 }));
    }
    refreshStatus();
    refreshUpnext();
  });
  return b;
}

// The browser only opens the microphone on a secure page (HTTPS, or localhost).
const VOICE_LIMIT_SEC = 25;

function voiceRecorder(limitSec) {
  const limit = limitSec || VOICE_LIMIT_SEC;
  const wrap = document.createElement("div");
  wrap.className = "voice-rec";
  const state = { blob: null, cleanup: () => {} };
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn";
  button.dataset.icon = "mic";
  button.textContent = t("dedication.voice_record");
  const blocked = !window.isSecureContext ? "dedication.voice_https"
    : !(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && typeof MediaRecorder !== "undefined")
      ? "dedication.voice_unsupported" : "";
  if (blocked) {
    button.disabled = true;
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = t(blocked);
    wrap.append(button, note);
    return { node: wrap, state };
  }
  const player = document.createElement("audio");
  player.controls = true;
  player.hidden = true;
  const drop = document.createElement("button");
  drop.type = "button";
  drop.className = "btn-link";
  drop.textContent = t("dedication.voice_remove");
  drop.hidden = true;
  let recorder = null;
  let stream = null;
  let timer = null;
  let started = 0;
  const finish = () => { if (recorder && recorder.state !== "inactive") recorder.stop(); };
  const tick = () => {
    const left = limit - Math.floor((Date.now() - started) / 1000);
    button.textContent = t("dedication.voice_stop", { s: Math.max(0, left) });
    if (left <= 0) finish();
  };
  state.cleanup = () => {
    finish();
    if (stream) stream.getTracks().forEach((track) => track.stop());
  };
  drop.addEventListener("click", () => {
    state.blob = null;
    player.hidden = true;
    drop.hidden = true;
    player.removeAttribute("src");
    button.textContent = t("dedication.voice_record");
  });
  button.addEventListener("click", async () => {
    if (recorder && recorder.state === "recording") {
      finish();
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      showToast(t("dedication.voice_denied"), t("dedication.voice_denied_hint"), { error: true });
      return;
    }
    const chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (event) => { if (event.data && event.data.size) chunks.push(event.data); };
    recorder.onstop = () => {
      clearInterval(timer);
      stream.getTracks().forEach((track) => track.stop());
      button.classList.remove("is-recording");
      const type = (recorder.mimeType || "audio/webm").split(";")[0];
      state.blob = chunks.length ? new Blob(chunks, { type }) : null;
      button.textContent = t(state.blob ? "dedication.voice_again" : "dedication.voice_record");
      if (state.blob) {
        player.src = URL.createObjectURL(state.blob);
        player.hidden = false;
        drop.hidden = false;
      }
    };
    recorder.start();
    started = Date.now();
    button.classList.add("is-recording");
    tick();
    timer = setInterval(tick, 250);
  });
  wrap.append(button, player, drop);
  return { node: wrap, state };
}

const NAME_VOICE_SEC = 6;

/* A name said in its owner's voice: recorded here (https only), or sent as a file by the owner. */
async function editNameVoice({ url, has, title, hint, file, limit }) {
  const box = document.createElement("div");
  box.className = "name-voice-box";
  const intro = document.createElement("p");
  intro.className = "hint";
  intro.textContent = hint;
  box.append(intro);
  if (has) {
    const label = document.createElement("p");
    label.className = "hint";
    label.textContent = t("name_voice.current");
    const current = document.createElement("audio");
    current.controls = true;
    current.preload = "none";
    current.src = url + (url.includes("?") ? "&" : "?") + "t=" + Date.now();
    box.append(label, current);
  }
  const recorder = voiceRecorder(limit || NAME_VOICE_SEC);
  box.append(recorder.node);
  let picker = null;
  if (file) {
    const label = document.createElement("label");
    label.className = "hint";
    label.textContent = t("name_voice.file");
    picker = document.createElement("input");
    picker.type = "file";
    picker.accept = "audio/*";
    label.append(document.createElement("br"), picker);
    box.append(label);
  }
  const choices = [{ label: t("name_voice.keep"), value: "save" }];
  if (has) choices.push({ label: t("name_voice.delete"), value: "delete", danger: true });
  const choice = await openModal({ title, bodyNode: box, choices, modalClass: "modal-dedication" });
  recorder.state.cleanup();
  if (choice === "delete") {
    const r = await apiDelete(url);
    if (!r.ok) showToolError(t("common.failed"), r);
    else showToast(t("name_voice.deleted"));
    return r.ok;
  }
  if (choice !== "save") return false;
  const blob = (picker && picker.files && picker.files[0]) || recorder.state.blob;
  if (!blob) {
    showToast(t("name_voice.nothing"), "", { error: true });
    return false;
  }
  const sent = await apiFetch(url, {
    method: "POST", headers: { "Content-Type": blob.type || "audio/webm" }, body: blob,
  });
  if (!sent.ok) {
    showToolError(t("common.failed"), sent);
    return false;
  }
  showToast(t("name_voice.saved"));
  return true;
}

// null: the reader closed the dialog, nothing is queued; otherwise {message, voice}.
async function askDedication() {
  const box = document.createElement("div");
  box.className = "dedication-box";
  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = t("dedication.hint");
  const area = document.createElement("textarea");
  area.className = "text-input";
  area.maxLength = 160;
  area.rows = 3;
  area.placeholder = t("dedication.placeholder");
  area.setAttribute("aria-label", t("dedication.title"));
  box.append(hint, area);
  const recorder = dedicationsVoice ? voiceRecorder() : null;
  if (recorder) box.append(recorder.node);
  const choice = await openModal({
    title: t("dedication.title"), bodyNode: box, modalClass: "modal-dedication",
    choices: [{ label: t("dedication.with"), value: "with" }, { label: t("dedication.without"), value: "without" }],
  });
  if (recorder) recorder.state.cleanup();
  if (!choice) return null;
  if (choice !== "with") return { message: "", voice: null };
  return { message: area.value.trim(), voice: recorder ? recorder.state.blob : null };
}

let dedicationPreview = null;

function dedicationLine(item) {
  const d = item.dedication;
  const p = document.createElement("span");
  p.className = "upnext-dedication";
  const from = d.from ? " - " + t("dedication.from", { name: d.from }) : "";
  if (d.text) p.textContent = "\u201c" + d.text + "\u201d" + (d.voice ? "" : from);
  if (d.voice) {
    const voice = document.createElement("span");
    voice.className = "upnext-voice";
    voice.dataset.icon = "mic";
    voice.textContent = t("dedication.voice_line") + from;
    if (d.text) p.append(document.createElement("br"));
    p.append(voice);
    if (document.body.dataset.access !== "guest") {
      const listen = document.createElement("button");
      listen.type = "button";
      listen.className = "btn-link";
      listen.textContent = t("dedication.voice_listen");
      listen.addEventListener("click", () => {
        if (dedicationPreview) dedicationPreview.pause();
        dedicationPreview = new Audio("/api/dedications/listen?key=" + encodeURIComponent(item.key));
        dedicationPreview.play().catch(() => showToast(t("common.failed")));
      });
      p.append(listen);
    }
  }
  if (document.body.dataset.access !== "guest") {
    const drop = document.createElement("button");
    drop.type = "button";
    drop.className = "btn-link";
    drop.textContent = t("dedication.remove");
    drop.addEventListener("click", async () => {
      const r = await apiPost("/api/dedications/delete", { key: item.key });
      if (!r.ok) {
        showToolError(t("common.failed"), r);
        return;
      }
      showToast(t("dedication.removed"));
      refreshUpnext();
    });
    p.append(drop);
  }
  return p;
}

/* Up next reordered by hand: a long press picks a song up, so a finger that moves at once still
   scrolls the page. The radio checks the move against its queue as it is then: a song change in
   between answers queue_changed and the list is read again. */
const UPNEXT_HOLD_MS = 450;
const UPNEXT_SLOP_PX = 8;
let upnextDrag = null;
let upnextStale = false;

function upnextMovable(list) {
  return [...list.children].filter((li) => li.classList.contains("is-movable"));
}

function upnextDragTo(y) {
  const drag = upnextDrag;
  const list = drag.li.parentNode;
  for (const li of upnextMovable(list)) {
    if (li === drag.li) continue;
    const box = li.getBoundingClientRect();
    if (y < box.top || y > box.bottom) continue;
    if (y < box.top + box.height / 2) list.insertBefore(drag.li, li);
    else list.insertBefore(drag.li, li.nextSibling);
    break;
  }
}

function upnextAutoScroll() {
  const drag = upnextDrag;
  if (!drag) return;
  const main = document.getElementById("main");
  const box = main.getBoundingClientRect();
  const edge = 48;
  const step = drag.y < box.top + edge ? -8 : drag.y > box.bottom - edge ? 8 : 0;
  if (step) {
    main.scrollTop += step;
    upnextDragTo(drag.y);
  }
  drag.frame = requestAnimationFrame(upnextAutoScroll);
}

function upnextPickUp(li, pointerId) {
  upnextDrag = { li, pointerId, y: upnextDrag.y, before: upnextDrag.before, frame: 0 };
  li.classList.add("is-dragging");
  li.parentNode.classList.add("is-sorting");
  try { li.setPointerCapture(pointerId); } catch (_) { /* the pointer already left */ }
  if (navigator.vibrate) navigator.vibrate(15);
  upnextDrag.frame = requestAnimationFrame(upnextAutoScroll);
}

async function upnextDrop(cancelled) {
  const drag = upnextDrag;
  upnextDrag = null;
  if (!drag || !drag.li.classList.contains("is-dragging")) return;
  cancelAnimationFrame(drag.frame);
  const li = drag.li;
  li.classList.remove("is-dragging");
  li.parentNode.classList.remove("is-sorting");
  const previous = li.previousElementSibling;
  const after = previous && previous.classList.contains("is-movable") ? previous.dataset.key : "";
  if (cancelled || after === drag.before) {
    if (upnextStale) refreshUpnext();
    return;
  }
  const r = await apiPost("/api/queue/move", { key: li.dataset.key, after });
  if (!r.ok) {
    if (r.error === "queue_changed") showToast(t("upnext.queue_changed"));
    else showToolError(t("common.failed"), r);
  }
  refreshUpnext();
}

function upnextMakeMovable(li) {
  li.classList.add("is-movable");
  let hold = 0;
  let start = null;
  const release = () => {
    clearTimeout(hold);
    hold = 0;
    start = null;
  };
  const letGo = () => {
    release();
    upnextDrag = null;
    if (upnextStale) refreshUpnext();
  };
  li.addEventListener("pointerdown", (e) => {
    if (upnextDrag || (e.pointerType === "mouse" && e.button !== 0) || e.target.closest("button, a")) return;
    start = { x: e.clientX, y: e.clientY };
    const previous = li.previousElementSibling;
    upnextDrag = { li, y: e.clientY, before: previous && previous.classList.contains("is-movable") ? previous.dataset.key : "" };
    hold = setTimeout(() => {
      hold = 0;
      if (upnextDrag && upnextDrag.li === li) upnextPickUp(li, e.pointerId);
    }, UPNEXT_HOLD_MS);
  });
  li.addEventListener("pointermove", (e) => {
    if (hold && start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > UPNEXT_SLOP_PX) {
      // Moving before the hold is over is a scroll: the song stays where it is.
      letGo();
      return;
    }
    if (upnextDrag && upnextDrag.li === li && li.classList.contains("is-dragging")) {
      upnextDrag.y = e.clientY;
      upnextDragTo(e.clientY);
    }
  });
  const end = (cancelled) => () => {
    if (hold) {
      letGo();
      return;
    }
    release();
    if (upnextDrag && upnextDrag.li === li) upnextDrop(cancelled);
  };
  li.addEventListener("pointerup", end(false));
  li.addEventListener("pointercancel", end(true));
  li.addEventListener("pointerleave", () => {
    if (hold) letGo();
  });
  // Once picked up, the finger moves the song, not the page.
  li.addEventListener("touchmove", (e) => {
    if (upnextDrag && upnextDrag.li === li && li.classList.contains("is-dragging")) e.preventDefault();
  }, { passive: false });
  li.addEventListener("contextmenu", (e) => {
    if (upnextDrag && upnextDrag.li === li) e.preventDefault();
  });
}

async function refreshUpnext() {
  // A song being carried is not swept away by a refresh: the list is read again once it is dropped.
  if (upnextDrag) {
    upnextStale = true;
    return;
  }
  upnextStale = false;
  const card = document.getElementById("upnextCard");
  const result = await apiGet("/api/queue");
  if (!result.ok || !result.data || !Array.isArray(result.data.items)) {
    card.hidden = true;
    return;
  }
  card.hidden = !result.data.enabled;
  if (!result.data.enabled) return;
  if (upnextDrag) {
    upnextStale = true;
    return;
  }
  const items = result.data.items;
  const mayMove = !!result.data.may_move;
  const fixed = Number(result.data.fixed) || 0;
  document.getElementById("upnextMoveHint").hidden = !mayMove || items.length - fixed < 2;
  document.getElementById("upnextList").replaceChildren(...items.map((item, i) => {
    const li = document.createElement("li");
    const rank = document.createElement("span");
    rank.className = "upnext-rank";
    rank.textContent = String(i + 1);
    const main = trackMain(item);
    if (item.dedication && (item.dedication.text || item.dedication.voice)) main.append(dedicationLine(item));
    li.append(rank, main);
    const mark = excludedMark(item);
    if (mark) li.append(mark);

    if (item.requested) {
      li.classList.add("is-requested");
      const tag = document.createElement("span");
      tag.className = "upnext-tag";
      tag.textContent = t("upnext.requested");
      li.append(tag);
    }
    if (item.key) li.append(libraryButton(item, false));
    if (mayMove && i >= fixed && item.key) {
      li.dataset.key = item.key;
      upnextMakeMovable(li);
    }
    return li;
  }));
  document.getElementById("upnextEmpty").hidden = items.length > 0;
}
refreshEvery(refreshUpnext, 60000, ["home/upnext"]);

const reminderWhen = document.getElementById("reminderWhen");
const REMINDER_MINUTES = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180];

function fillReminderWhen() {
  const keep = reminderWhen.value || "10";
  const options = REMINDER_MINUTES.map((n) => {
    const o = document.createElement("option");
    o.value = String(n);
    o.textContent = n >= 60 && n % 60 === 0 ? t("reminders.in_hours", { n: n / 60 }) : t("reminders.in_min", { n });
    return o;
  });
  const at = document.createElement("option");
  at.value = "at";
  at.textContent = t("reminders.at_time");
  reminderWhen.replaceChildren(...options, at);
  reminderWhen.value = keep;
}
fillReminderWhen();
window.LANG_CHANGE_LISTENERS.push(fillReminderWhen);
reminderWhen.addEventListener("change", () => {
  document.getElementById("reminderAtRow").hidden = reminderWhen.value !== "at";
});

function reminderTime(at) {
  const when = new Date(at * 1000);
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return when.toDateString() === new Date().toDateString() ? time : t("reminders.tomorrow", { time });
}

async function refreshReminders() {
  const r = await apiGet("/api/reminders");
  if (!r.ok) return;
  const items = (r.data && r.data.items) || [];
  document.getElementById("reminderList").replaceChildren(...items.map((item) => {
    const li = document.createElement("li");
    const main = document.createElement("span");
    main.className = "recent-main";
    const when = document.createElement("span");
    when.className = "reminder-when";
    when.textContent = reminderTime(item.at);
    const text = document.createElement("span");
    text.textContent = item.text;
    main.append(when, text);
    const drop = document.createElement("button");
    drop.type = "button";
    drop.className = "btn btn-icon btn-small";
    drop.dataset.icon = "trash";
    drop.setAttribute("aria-label", t("reminders.delete"));
    drop.title = t("reminders.delete");
    drop.addEventListener("click", async () => {
      const done = await apiDelete("/api/reminders/" + encodeURIComponent(item.id));
      if (!done.ok) showToolError(t("common.failed"), done);
      else showToast(t("reminders.deleted"));
      refreshReminders();
    });
    li.append(main, drop);
    return li;
  }));
  document.getElementById("reminderEmpty").hidden = items.length > 0;
}
refreshEvery(refreshReminders, 30000, ["home/reminders"]);

document.getElementById("reminderForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const textField = document.getElementById("reminderText");
  const body = { text: textField.value.trim() };
  if (reminderWhen.value === "at") body.time = document.getElementById("reminderAt").value;
  else body.minutes = Number(reminderWhen.value);
  const r = await apiPost("/api/reminders", body);
  if (!r.ok) {
    showToolError(t("common.failed"), r);
    return;
  }
  textField.value = "";
  const list = await apiGet("/api/reminders");
  const added = list.ok && list.data ? (list.data.items || []).find((i) => i.id === (r.data && r.data.id)) : null;
  showToast(added ? t("reminders.added", { time: reminderTime(added.at) }) : t("common.ok"));
  refreshReminders();
});

async function refreshMemories() {
  const box = document.getElementById("memoriesList");
  const r = await apiGet("/api/memories");
  const items = r.ok && Array.isArray(r.data) ? r.data : [];
  box.hidden = items.length === 0;
  box.replaceChildren(...items.map((item) => {
    const li = document.createElement("li");
    const text = document.createElement("span");
    text.className = "memory-text";
    const when = item.years ? t(item.years === 1 ? "memory.year" : "memory.years", { n: item.years })
      : t("memory.month");
    const song = item.artist ? item.title + " - " + item.artist : item.title;
    text.textContent = t("memory.liked", { when, song });
    li.append(text, libraryButton(item, true));
    return li;
  }));
}
refreshEvery(refreshMemories, 600000, ["home/upnext"]);

const librarySearch = document.getElementById("librarySearch");
const libraryArtist = document.getElementById("libraryArtist");
const libraryAlbum = document.getElementById("libraryAlbum");
const libraryGenre = document.getElementById("libraryGenre");
let libraryOffset = 0;
let librarySeq = 0;
let libraryTimer = null;
let libraryCatalogue = "";

function fillFacet(select, entries, allKey) {
  const keep = select.value;
  const all = document.createElement("option");
  all.value = "";
  all.dataset.i18n = allKey;
  all.textContent = t(allKey);
  select.replaceChildren(all, ...entries.map((e) => {
    const o = document.createElement("option");
    o.value = e.name;
    o.textContent = e.name + " (" + e.count + ")";
    return o;
  }));
  select.value = entries.some((e) => e.name === keep) ? keep : "";
}

async function refreshLibraryFacets() {
  const q = libraryArtist.value ? "?artist=" + encodeURIComponent(libraryArtist.value) : "";
  const r = await apiGet("/api/library/facets" + q);
  if (!r.ok || !r.data) return;
  fillFacet(libraryArtist, r.data.artists || [], "library.all_artists");
  fillFacet(libraryAlbum, r.data.albums || [], "library.all_albums");
  fillFacet(libraryGenre, r.data.genres || [], "library.all_genres");
  libraryGenre.hidden = !(r.data.genres || []).length;
  paintGenrePlay();
}

function libraryAsked() {
  return librarySearch.value.trim() || libraryArtist.value || libraryAlbum.value || libraryGenre.value;
}

async function refreshLibrary(more) {
  const seq = ++librarySeq;
  const card = document.getElementById("libraryCard");
  const list = document.getElementById("libraryList");
  const params = new URLSearchParams();
  if (librarySearch.value.trim()) params.set("q", librarySearch.value.trim());
  if (libraryArtist.value) params.set("artist", libraryArtist.value);
  if (libraryAlbum.value) params.set("album", libraryAlbum.value);
  if (libraryGenre.value) params.set("genre", libraryGenre.value);
  libraryOffset = more ? libraryOffset : 0;
  params.set("offset", String(libraryOffset));
  const r = await apiGet("/api/library?" + params.toString());
  if (seq !== librarySeq) return;
  if (!r.ok || !r.data) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const d = r.data;
  const status = d.status || {};
  const count = document.getElementById("libraryCount");
  const asked = libraryAsked();

  const reading = status.total && status.read < status.total
    ? " " + t("library.reading", { n: status.read, total: status.total }) : "";
  count.textContent = (asked ? t(d.total === 1 ? "library.found_one" : "library.found", { n: d.total })
    : t("library.size", { n: status.total || 0 })) + reading;
  const signature = status.total + "/" + status.read;
  if (signature !== libraryCatalogue) {
    libraryCatalogue = signature;
    refreshLibraryFacets();
  }
  if (!asked) {
    list.replaceChildren();
    document.getElementById("libraryMore").hidden = true;
    return;
  }
  const rows = d.items.map((item) => {
    const li = document.createElement("li");
    li.append(trackMain(item));
    const mark = excludedMark(item);
    if (mark) li.append(mark);
    const actions = document.createElement("span");
    actions.className = "library-actions";
    if (document.body.dataset.access !== "guest") actions.append(libraryListButton(item));
    actions.append(libraryButton(item, true));
    li.append(actions);
    return li;
  });
  if (more) list.append(...rows);
  else list.replaceChildren(...rows);
  libraryOffset += d.items.length;
  document.getElementById("libraryMore").hidden = libraryOffset >= d.total;
}

librarySearch.addEventListener("input", () => {
  clearTimeout(libraryTimer);
  libraryTimer = setTimeout(() => refreshLibrary(false), 300);
});
libraryArtist.addEventListener("change", async () => {
  libraryAlbum.value = "";
  await refreshLibraryFacets();
  refreshLibrary(false);
});
libraryAlbum.addEventListener("change", () => refreshLibrary(false));
libraryGenre.addEventListener("change", () => {
  refreshLibrary(false);
  paintGenrePlay();
});
document.getElementById("libraryMore").addEventListener("click", () => refreshLibrary(true));
refreshLibrary(false);

/* Opens the library already filtered: {q} searches (a file name finds its song), {artist} picks the artist. */
async function showInLibrary(filter) {
  librarySearch.value = filter.q || "";
  libraryArtist.value = "";
  libraryAlbum.value = "";
  libraryGenre.value = "";
  if (filter.artist) {
    await refreshLibraryFacets();
    libraryArtist.value = filter.artist;
    if (libraryArtist.value === filter.artist) await refreshLibraryFacets();
    else librarySearch.value = filter.artist;
  }
  paintGenrePlay();
  setActiveView("home", "library");
  refreshLibrary(false);
}

/* What the catalogue does in the background - finding new files, reading their tags, their
   endings, their loudness - shown in the side menu while it lasts: songs just sent can take a
   while, and nothing else would say so. */
async function refreshLibraryWork() {
  if (!mayCall("/api/library/work")) return;
  const r = await apiGet("/api/library/work");
  const box = document.getElementById("navLibrary");
  const d = r.ok && r.data ? r.data : {};
  box.hidden = !d.stage || (d.left === 0);
  if (box.hidden) return;
  const left = d.left === null || d.left === undefined ? null : d.left;
  document.getElementById("navLibraryBar").style.width =
    left === null || !d.total ? "0%" : Math.round(100 * (d.total - left) / d.total) + "%";
  document.getElementById("navLibraryLine").textContent = left === null
    ? t("libwork.scan") : t("libwork." + d.stage, { n: left });
}

document.getElementById("navLibrary").addEventListener("click", () => setActiveView("home", "library"));
refreshLibraryWork();
refreshEvery(refreshLibraryWork, 15000);
