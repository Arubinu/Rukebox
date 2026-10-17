/* Web radios: a name and a stream address, played in place of the radio's own music. */
function stationRow(station) {
  const li = document.createElement("li");
  const label = document.createElement("span");
  const name = document.createElement("span");
  name.className = "device-name";
  name.textContent = station.name;
  const sub = document.createElement("span");
  sub.className = "device-sub";
  sub.textContent = station.url;
  label.append(name, sub);
  const actions = document.createElement("span");
  actions.className = "device-actions";
  const play = document.createElement("button");
  play.type = "button";
  play.className = "btn station-play";
  play.dataset.icon = "play";
  play.textContent = t("stations.play");
  play.addEventListener("click", async () => {
    play.disabled = true;
    const result = await apiPost("/api/stations/" + encodeURIComponent(station.id) + "/play");
    play.disabled = false;
    if (!result.ok) showError(result.error);
    else showToast(t("stations.playing", { name: station.name }));
    refreshStatus();
  });
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn btn-warning-outline station-remove";
  remove.textContent = t("stations.remove");
  remove.addEventListener("click", async () => {
    const ok = await openModal({ title: t("stations.remove_title", { name: station.name }),
                                 body: t("stations.remove_body"), confirm: t("stations.remove") });
    if (!ok) return;
    const result = await apiDelete("/api/stations/" + encodeURIComponent(station.id));
    if (!result.ok) showError(result.error);
    refreshStations();
  });
  actions.append(play, remove);
  li.append(label, actions);
  return li;
}

async function refreshStations() {
  if (!mayCall("/api/stations")) return;
  const result = await apiGet("/api/stations");
  if (!result.ok || !result.data) return;
  stationsCache = result.data.stations || [];
  const list = document.getElementById("stationsList");
  list.replaceChildren(...stationsCache.map(stationRow));
  list.hidden = !stationsCache.length;
  document.getElementById("stationsEmpty").hidden = !!stationsCache.length;
  const select = document.getElementById("schedStation");
  fillScheduleStations(select.value);
  updateScheduleForm();
}

document.getElementById("stationAdd").addEventListener("click", async () => {
  const name = document.getElementById("stationName");
  const url = document.getElementById("stationUrl");
  const result = await apiPost("/api/stations", { name: name.value.trim(), url: url.value.trim() });
  if (!result.ok) {
    showError(result.error);
    return;
  }
  name.value = "";
  url.value = "";
  showToast(t("stations.added", { name: result.data.name }));
  refreshStations();
});

refreshStations();
