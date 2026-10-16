function paintLastCard(card) {
  lastCardSeen = card || null;
  const row = document.getElementById("cardLastRow");
  row.hidden = !card;
  if (!card) return;
  const text = card.known
    ? t("cards.last_known", { name: card.name }) + (card.error && card.error !== "card_unknown" ? " - " + errorLabel(card.error) : "")
    : t("cards.last_unknown", { id: card.id });
  document.getElementById("cardLastText").textContent = text;
  document.getElementById("btnCardRegister").hidden = !!card.known;
}

const CARD_ACTION_ROWS = { list: "cardListRow", folder: "cardFolderRow", announcement: "cardAnnRow", action: "cardClickRow" };
function paintCardAction() {
  const action = document.getElementById("cardAction").value;
  Object.entries(CARD_ACTION_ROWS).forEach(([key, id]) => { document.getElementById(id).hidden = key !== action; });
}
document.getElementById("cardAction").addEventListener("change", paintCardAction);

function cardTargetLabel(card, lists, anns) {
  if (card.action === "list") {
    const list = lists.find((l) => l.id === card.target);
    return t("cards.action_list") + " : " + (card.target ? (list ? list.name : card.target) : t("cards.everything"));
  }
  if (card.action === "folder") return t("cards.action_folder") + " : " + card.target.split("/").pop();
  if (card.action === "announcement") {
    const ann = anns.find((a) => a.id === card.target);
    return t("cards.action_announcement") + " : " + (ann ? ann.name : card.target);
  }
  return t("cards.action_click") + " : " + t("click." + card.target);
}

async function refreshCards() {
  const [r, listsR, annsR] = await Promise.all([apiGet("/api/cards"), apiGet("/api/lists"), apiGet("/api/announcements")]);
  if (!r.ok || !Array.isArray(r.data)) return;
  const lists = listsR.ok && listsR.data ? listsR.data.lists || [] : [];
  const anns = annsR.ok && Array.isArray(annsR.data) ? annsR.data : [];
  const listSelect = document.getElementById("cardList");
  const keepList = listSelect.value;
  listSelect.replaceChildren(new Option(t("cards.everything"), ""), ...lists.map((l) => new Option(l.name, l.id)));
  listSelect.value = keepList;
  const annSelect = document.getElementById("cardAnn");
  const keepAnn = annSelect.value;
  annSelect.replaceChildren(...anns.map((a) => new Option(a.name, a.id)));
  if (keepAnn) annSelect.value = keepAnn;
  const clickSelect = document.getElementById("cardClick");
  if (!clickSelect.options.length) {
    [...document.getElementById("singleClickAction").options]
      .filter((o) => o.value !== "off")
      .forEach((o) => clickSelect.appendChild(new Option(o.textContent, o.value)));
  }

  const list = document.getElementById("cardsList");
  list.replaceChildren(...r.data.map((card) => {
    const li = document.createElement("li");
    const text = document.createElement("div");
    text.className = "ann-text";
    const name = document.createElement("span");
    name.className = "ann-name";
    name.textContent = card.name;
    const meta = document.createElement("span");
    meta.className = "ann-meta";
    meta.textContent = cardTargetLabel(card, lists, anns) + " · " + card.id;
    text.append(name, meta);
    const play = document.createElement("button");
    play.type = "button";
    play.className = "btn btn-icon btn-small";
    play.dataset.icon = "play";
    play.setAttribute("aria-label", t("cards.try"));
    play.title = t("cards.try");
    play.addEventListener("click", async () => {
      const res = await apiPost("/api/cards/" + encodeURIComponent(card.id) + "/play", {});
      if (!res.ok) showError(res.error, card.name);
    });
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn btn-icon btn-small btn-danger-outline";
    del.dataset.icon = "trash";
    del.setAttribute("aria-label", t("common.delete"));
    del.addEventListener("click", async () => {
      if (!(await showConfirm(t("cards.delete_confirm", { name: card.name })))) return;
      const res = await apiDelete("/api/cards/" + encodeURIComponent(card.id));
      if (!res.ok) showError(res.error, card.name);
      refreshCards();
    });
    li.append(text, play, del);
    return li;
  }));
  document.getElementById("cardsEmpty").hidden = r.data.length > 0;
}

document.getElementById("btnCardRegister").addEventListener("click", () => {
  if (!lastCardSeen) return;
  document.getElementById("cardId").value = lastCardSeen.id;
  document.getElementById("cardFormSection").open = true;
  document.getElementById("cardName").focus();
});

document.getElementById("btnCardFolderBrowse").addEventListener("click", () => {
  const input = document.getElementById("cardFolder");
  folderPickerTarget = input;
  document.getElementById("folderOverlay").hidden = false;
  showFolder(input.value.trim());
});

document.getElementById("cardForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const action = document.getElementById("cardAction").value;
  const target = { list: "cardList", folder: "cardFolder", announcement: "cardAnn", action: "cardClick" }[action];
  const r = await apiPost("/api/cards", {
    id: document.getElementById("cardId").value.trim(),
    name: document.getElementById("cardName").value.trim(),
    action,
    target: document.getElementById(target).value.trim(),
  });
  if (!r.ok) {
    showError(r.error, t("cards.title"));
    return;
  }
  showToast(t("cards.saved", { name: r.data.name }));
  document.getElementById("cardForm").reset();
  paintCardAction();
  document.getElementById("cardFormSection").open = false;
  refreshCards();
});

refreshEvery(refreshCards, 30000, ["settings/cards"]);
window.LANG_CHANGE_LISTENERS.push(() => {
  renderLikes();
  paintLikeButton();
});

/* Duplicate tracks: the same song catalogued more than once, grouped by title and artist. */
let duplicateGroups = [];
let duplicatesAsked = false;
let duplicatesShowPath = false;

function duplicateFacts(copy) {
  return [copy.album, formatBytes(copy.size), formatClock(copy.duration),
    copy.kbps ? copy.kbps + " kb/s" : ""].filter(Boolean).join(" \u00b7 ");
}

