/* Tags and covers: a song's tags written into its file, and the pictures shown for it (src/tag_editor.py). */
const TAG_FIELDS = ["title", "artist", "album", "album_artist", "year", "genre", "track"];
const TAG_INPUT = {
  title: "tagsTitleInput", artist: "tagsArtistInput", album: "tagsAlbumInput",
  album_artist: "tagsAlbumArtistInput", year: "tagsYearInput", genre: "tagsGenreInput",
  track: "tagsTrackInput",
};
const tagsState = { key: null, data: null, level: null, searchTimer: null, searchSeq: 0, stamp: 0 };

function tagsSearchRow(item) {
  const row = document.createElement("li");
  row.className = "tags-result";
  const open = document.createElement("button");
  open.type = "button";
  open.className = "tags-result-open";
  open.dataset.icon = "music";
  const title = document.createElement("strong");
  title.textContent = item.title || "";
  const meta = document.createElement("span");
  meta.className = "tags-result-meta";
  meta.textContent = [item.artist, item.album].filter(Boolean).join(" · ");
  open.append(title, meta);
  open.addEventListener("click", () => openTags(item.key));
  row.append(open);
  return row;
}

async function searchTags() {
  const words = document.getElementById("tagsSearch").value.trim();
  const list = document.getElementById("tagsResults");
  const seq = ++tagsState.searchSeq;
  if (!words) {
    list.replaceChildren();
    return;
  }
  const result = await apiGet("/api/library?q=" + encodeURIComponent(words));
  if (seq !== tagsState.searchSeq || !result.ok) return;
  const items = (result.data && result.data.items) || [];
  list.replaceChildren(...items.map(tagsSearchRow));
  if (!items.length) {
    const empty = document.createElement("li");
    empty.className = "hint";
    empty.textContent = t("tags.no_match");
    list.append(empty);
  }
}

document.getElementById("tagsSearch").addEventListener("input", () => {
  clearTimeout(tagsState.searchTimer);
  tagsState.searchTimer = setTimeout(searchTags, 300);
});

async function openTags(key) {
  const result = await apiGet("/api/tags?key=" + encodeURIComponent(key));
  if (!result.ok) {
    showError(result.error);
    return;
  }
  tagsState.key = key;
  paintTags(result.data);
  document.getElementById("tagsCandidates").hidden = true;
  document.getElementById("tagsForm").scrollIntoView({ block: "nearest" });
}

function paintTags(data) {
  tagsState.data = data;
  tagsState.key = data.key;
  tagsState.stamp = Date.now();
  const form = document.getElementById("tagsForm");
  form.hidden = false;
  document.getElementById("tagsPath").textContent = data.path;
  TAG_FIELDS.forEach((name) => {
    const input = document.getElementById(TAG_INPUT[name]);
    input.value = data.tags[name] || "";
    input.disabled = !data.writable;
  });
  document.getElementById("tagsAlbumWide").checked = false;
  document.getElementById("tagsReadOnly").hidden = data.writable;
  form.querySelector("button[type=submit]").disabled = !data.writable;
  const identify = document.getElementById("tagsIdentify");
  identify.disabled = !data.writable || !data.identify.tool || !data.identify.key;
  const why = document.getElementById("tagsIdentifyWhy");
  why.hidden = data.identify.tool && data.identify.key;
  why.textContent = !data.identify.tool ? t("tags.identify_no_tool") : t("tags.identify_no_key");
  paintTagPictures(data);
}

function tagPictureSlot(level, title, detail, present, canChange) {
  const data = tagsState.data;
  const slot = document.createElement("figure");
  slot.className = "tags-picture";
  slot.dataset.level = level;
  const frame = document.createElement("span");
  frame.className = "tags-picture-frame";
  frame.dataset.icon = "image";
  if (present) {
    const img = document.createElement("img");
    img.alt = "";
    img.src = "/api/tags/picture?key=" + encodeURIComponent(data.key) + "&level=" + level
      + "&t=" + tagsState.stamp;
    img.addEventListener("error", () => img.remove());
    frame.append(img);
  }
  const caption = document.createElement("figcaption");
  const name = document.createElement("strong");
  name.textContent = title;
  const where = document.createElement("span");
  where.className = "tags-picture-where";
  where.textContent = detail;
  caption.append(name, where);
  const actions = document.createElement("div");
  actions.className = "tags-picture-actions";
  if (canChange && level !== "beside") {
    const replace = document.createElement("button");
    replace.type = "button";
    replace.className = "btn btn-small";
    replace.dataset.icon = "upload";
    replace.textContent = present ? t("tags.picture_replace") : t("tags.picture_add");
    replace.addEventListener("click", () => {
      tagsState.level = level;
      document.getElementById("tagsPictureInput").click();
    });
    actions.append(replace);
  }
  if (canChange && present) {
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn-icon btn-small";
    remove.dataset.icon = "trash";
    remove.title = t("tags.picture_remove");
    remove.setAttribute("aria-label", t("tags.picture_remove"));
    remove.addEventListener("click", () => removeTagPicture(level));
    actions.append(remove);
  }
  slot.append(frame, caption, actions);
  return slot;
}

function paintTagPictures(data) {
  const box = document.getElementById("tagsPictures");
  const slots = [tagPictureSlot("file", t("tags.picture_file"), t("tags.picture_file_detail"),
                                data.picture, data.writable)];
  if (data.beside) {
    slots.push(tagPictureSlot("beside", t("tags.picture_beside"), data.beside, true, data.writable));
  }
  [["album", "tags.picture_album"], ["artist", "tags.picture_artist"]].forEach(([level, key]) => {
    const folder = data.folders[level];
    if (!folder) return;
    const where = (folder.folder || t("tags.music_folder")) + (folder.picture ? " / " + folder.picture : "");
    slots.push(tagPictureSlot(level, t(key), where, !!folder.picture, folder.writable));
  });
  box.replaceChildren(...slots);
  // Which one the player shows: the order of src/track_media.py.
  const order = data.beside ? ["beside"] : [];
  const folders = ["album", "artist"].filter((level) => data.folders[level] && data.folders[level].picture);
  const shown = data.priority === "id3"
    ? order.concat(data.picture ? ["file"] : [], folders)
    : order.concat(folders, data.picture ? ["file"] : []);
  const winner = box.querySelector('[data-level="' + (shown[0] || "") + '"]');
  if (winner) winner.classList.add("is-shown");
}

document.getElementById("tagsPictureInput").addEventListener("change", async (event) => {
  const file = (event.target.files || [])[0];
  event.target.value = "";
  if (!file || !tagsState.key || !tagsState.level) return;
  const body = new FormData();
  body.append("key", tagsState.key);
  body.append("level", tagsState.level);
  body.append("file", file, file.name);
  await sendTagPicture(body);
});

async function sendTagPicture(body) {
  const result = await apiFetch("/api/tags/picture", { method: "POST", body });
  if (!result.ok) {
    showError(result.error);
    return false;
  }
  paintTags(result.data);
  showToast(t("tags.picture_saved"));
  return true;
}

async function removeTagPicture(level) {
  if (!(await showConfirm(t("tags.picture_confirm_remove")))) return;
  const result = await apiDelete("/api/tags/picture?key=" + encodeURIComponent(tagsState.key)
                                 + "&level=" + level);
  if (!result.ok) {
    showError(result.error);
    return;
  }
  paintTags(result.data);
  showToast(t("tags.picture_removed"));
}

document.getElementById("tagsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!tagsState.data) return;
  const tags = {};
  TAG_FIELDS.forEach((name) => {
    const value = document.getElementById(TAG_INPUT[name]).value.trim();
    if (value !== (tagsState.data.tags[name] || "")) tags[name] = value;
  });
  const album = document.getElementById("tagsAlbumWide").checked;
  if (!Object.keys(tags).length) {
    showToast(t("alert.no_changes"));
    return;
  }
  const button = event.target.querySelector("button[type=submit]");
  button.disabled = true;
  const result = await apiPost("/api/tags", { key: tagsState.key, tags, album });
  button.disabled = false;
  if (!result.ok) {
    showError(result.error);
    return;
  }
  paintTags(result.data);
  const failed = result.data.album_failed || [];
  if (failed.length) {
    showToast(t("tags.saved_album_partly", { done: result.data.album_done, failed: failed.length }),
              failed.map((one) => one.name).join(", "), { error: true });
  } else if (album) {
    showToast(t("tags.saved_album", { n: result.data.album_done + 1 }));
  } else {
    showToast(t("tags.saved"));
  }
  searchTags();
});

function tagCandidateRow(candidate) {
  const row = document.createElement("li");
  row.className = "tags-candidate";
  const text = document.createElement("div");
  text.className = "tags-candidate-text";
  const title = document.createElement("strong");
  title.textContent = candidate.title;
  const meta = document.createElement("span");
  meta.textContent = [candidate.artist, candidate.album, candidate.year].filter(Boolean).join(" · ")
    + " · " + Math.round(candidate.score * 100) + " %";
  text.append(title, meta);
  const actions = document.createElement("div");
  actions.className = "tags-picture-actions";
  const fill = document.createElement("button");
  fill.type = "button";
  fill.className = "btn btn-small";
  fill.dataset.icon = "pencil";
  fill.textContent = t("tags.candidate_fill");
  fill.addEventListener("click", () => {
    ["title", "artist", "album", "year"].forEach((name) => {
      if (candidate[name]) document.getElementById(TAG_INPUT[name]).value = candidate[name];
    });
    showToast(t("tags.candidate_filled"));
  });
  actions.append(fill);
  if (candidate.release_group) {
    const cover = document.createElement("button");
    cover.type = "button";
    cover.className = "btn btn-small";
    cover.dataset.icon = "image";
    cover.textContent = t("tags.candidate_cover");
    cover.addEventListener("click", async () => {
      const level = tagsState.data.folders.album ? "album" : "file";
      const body = new FormData();
      body.append("key", tagsState.key);
      body.append("level", level);
      body.append("release_group", candidate.release_group);
      cover.disabled = true;
      await sendTagPicture(body);
      cover.disabled = false;
    });
    actions.append(cover);
  }
  row.append(text, actions);
  return row;
}

document.getElementById("tagsIdentify").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const list = document.getElementById("tagsCandidates");
  button.disabled = true;
  const result = await apiPost("/api/tags/identify", { key: tagsState.key });
  button.disabled = false;
  if (!result.ok) {
    showError(result.error);
    return;
  }
  const found = result.data.candidates || [];
  list.replaceChildren(...found.map(tagCandidateRow));
  if (!found.length) {
    const empty = document.createElement("li");
    empty.className = "hint";
    empty.textContent = t("tags.identify_nothing");
    list.append(empty);
  }
  list.hidden = false;
});
