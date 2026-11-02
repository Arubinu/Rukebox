const networkButtonList = document.getElementById("networkButtonList");

function networkButtonRow(button) {
  const li = document.createElement("li");
  const label = document.createElement("span");
  const name = document.createElement("input");
  name.type = "text";
  name.className = "text-input";
  name.maxLength = 40;
  name.value = button.name || "";
  name.setAttribute("aria-label", t("netbutton.name"));
  name.addEventListener("change", async () => {
    const r = await apiPost("/api/network_buttons/" + encodeURIComponent(button.id), { name: name.value });
    if (!r.ok) showError(r.error);
    refreshNetworkButtons();
  });
  const sub = document.createElement("span");
  sub.className = "device-sub";
  const address = document.createElement("span");
  address.className = "device-mac";
  address.textContent = button.ip || "";
  const state = document.createElement("span");
  state.className = button.approved ? "badge connected" : "badge";
  state.textContent = t(button.approved ? "netbutton.approved" : "netbutton.waiting");
  sub.append(address, state);
  if (button.seen) {
    const seen = document.createElement("span");
    seen.className = "device-mac";
    seen.textContent = t("netbutton.seen", { time: new Date(button.seen * 1000).toLocaleTimeString(currentLang) });
    sub.appendChild(seen);
  }
  label.append(name, sub);
  const actions = document.createElement("span");
  actions.className = "device-actions";
  if (!button.approved) {
    const approve = document.createElement("button");
    approve.type = "button";
    approve.className = "btn";
    approve.textContent = t("netbutton.approve");
    approve.addEventListener("click", async () => {
      const r = await apiPost("/api/network_buttons/" + encodeURIComponent(button.id), { approved: true });
      if (!r.ok) showError(r.error);
      refreshNetworkButtons();
    });
    actions.appendChild(approve);
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn btn-icon btn-small";
  remove.dataset.icon = "x";
  remove.title = t("netbutton.remove", { name: button.name || "" });
  remove.setAttribute("aria-label", remove.title);
  remove.addEventListener("click", async () => {
    const r = await apiDelete("/api/network_buttons/" + encodeURIComponent(button.id));
    if (!r.ok) showError(r.error);
    refreshNetworkButtons();
  });
  actions.appendChild(remove);
  li.append(label, actions);
  return li;
}

async function refreshNetworkButtons() {
  // A name being typed is not redrawn under the fingers.
  if (networkButtonList.contains(document.activeElement)) return;
  const result = await apiGet("/api/network_buttons");
  if (!result.ok || !result.data) return;
  const buttons = result.data.buttons || [];
  networkButtonList.replaceChildren(...buttons.map(networkButtonRow));
  networkButtonList.hidden = !buttons.length;
  document.getElementById("networkButtonEmpty").hidden = buttons.length > 0;
}

// A part this late registers after the page's first arrival: it reads once on its own.
refreshEvery(refreshNetworkButtons, 5000, ["settings/buttons"]);
refreshNetworkButtons();
