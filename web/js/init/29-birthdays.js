/* The birthdays of the house: the radio wishes them on the day, at the first song or on a button. */
const birthdayList = document.getElementById("birthdayList");
const birthdayWish = document.getElementById("birthdayWish");
const birthdayDateBox = document.getElementById("birthdayDate");

function paintBirthdayDate() {
  const now = new Date();
  const today = String(now.getMonth() + 1).padStart(2, "0") + "-" + String(now.getDate()).padStart(2, "0");
  birthdayDateBox.replaceChildren(dayMonthPicker(today, t("birthdays.date")));
}

function birthdayRow(item) {
  const li = document.createElement("li");
  const main = document.createElement("div");
  main.className = "recent-main";
  const name = document.createElement("span");
  name.className = "recent-title";
  name.textContent = item.name;
  const when = document.createElement("span");
  when.className = "recent-sub";
  when.textContent = seasonDateLabel(item.date);
  main.append(name, when);
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn btn-icon btn-small";
  remove.dataset.icon = "x";
  remove.title = t("birthdays.remove", { name: item.name });
  remove.setAttribute("aria-label", remove.title);
  remove.addEventListener("click", async () => {
    const r = await apiDelete("/api/birthdays/" + encodeURIComponent(item.id));
    if (!r.ok) {
      showError(r.error);
      return;
    }
    refreshBirthdays();
  });
  li.append(main, remove);
  return li;
}

async function refreshBirthdays() {
  const result = await apiGet("/api/birthdays");
  if (!result.ok || !result.data) return;
  const people = result.data.people || [];
  birthdayWish.value = result.data.wish || "start";
  birthdayList.replaceChildren(...people.map(birthdayRow));
  document.getElementById("birthdayEmpty").hidden = people.length > 0;
}

birthdayWish.addEventListener("change", async () => {
  const r = await apiPost("/api/birthdays", { wish: birthdayWish.value });
  if (!r.ok) {
    showError(r.error);
    refreshBirthdays();
    return;
  }
  showToast(t("birthdays.wish_saved"));
});

document.getElementById("birthdayForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = document.getElementById("birthdayName");
  const r = await apiPost("/api/birthdays", { name: name.value, date: readDayMonth(birthdayDateBox) });
  if (!r.ok) {
    showError(r.error);
    return;
  }
  showToast(t("birthdays.added", { name: r.data.name, date: seasonDateLabel(r.data.date) }));
  name.value = "";
  refreshBirthdays();
});

paintBirthdayDate();
window.LANG_CHANGE_LISTENERS.push(paintBirthdayDate);
refreshEvery(refreshBirthdays, 60000, ["home/birthdays"]);
// The page may already be on screen: its arrival was announced before this part ran.
refreshBirthdays();
