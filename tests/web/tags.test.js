"use strict";
// Tags and covers (web/js/init/26-tags.js): a song found, its fields filled in, only what
// changed sent, and the picture the player shows outlined among the others.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until } = require("./harness");

const SONG = {
  key: "k1", path: "LMFAO/Sorry/01 - Party.opus", writable: true, picture: true, beside: null,
  duration: 200, priority: "files", identify: { tool: true, key: false },
  tags: { title: "Party", artist: "LMFAO", album: "Sorry", album_artist: "", year: "2011",
          genre: "", track: "1" },
  folders: { album: { folder: "LMFAO/Sorry", picture: "cover.jpg", writable: true },
             artist: { folder: "LMFAO", picture: null, writable: true } },
};

function page() {
  return load({ hash: "#home/tags", view: "detailed", routes: {
    "GET /api/library": { items: [{ key: "k1", title: "Party", artist: "LMFAO", album: "Sorry" }], total: 1 },
    "GET /api/tags": SONG,
    "POST /api/tags": Object.assign({}, SONG, { album_done: 0, album_failed: [] }),
  } });
}

test("a song found opens with its fields, and only what changed is sent", async () => {
  const p = page();
  await until(() => p.$("bootOverlay").hidden);
  p.$("tagsSearch").value = "party";
  p.$("tagsSearch").dispatchEvent(new p.window.Event("input"));
  await until(() => p.document.querySelectorAll("#tagsResults .tags-result-open").length === 1);
  p.document.querySelector("#tagsResults .tags-result-open").click();
  await until(() => !p.$("tagsForm").hidden);
  assert.equal(p.$("tagsTitleInput").value, "Party");
  assert.equal(p.$("tagsYearInput").value, "2011");
  assert.equal(p.$("tagsPath").textContent, "LMFAO/Sorry/01 - Party.opus");
  assert.equal(p.$("tagsIdentify").disabled, true, "no AcoustID key yet");
  assert.equal(p.$("tagsIdentifyWhy").hidden, false);

  const slots = [...p.document.querySelectorAll("#tagsPictures .tags-picture")];
  assert.deepEqual(slots.map((one) => one.dataset.level), ["file", "album", "artist"]);
  assert.equal(p.document.querySelector("#tagsPictures .is-shown").dataset.level, "album",
               "the folder's picture wins over the file's by default");

  p.$("tagsTitleInput").value = "Party Rock Anthem";
  p.$("tagsAlbumWide").checked = true;
  p.$("tagsForm").dispatchEvent(new p.window.Event("submit", { cancelable: true }));
  await until(() => p.sent("POST", "/api/tags").length);
  assert.deepEqual(p.sent("POST", "/api/tags")[0].body,
                   { key: "k1", tags: { title: "Party Rock Anthem" }, album: true });
  await p.close();
});
