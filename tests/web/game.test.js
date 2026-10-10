"use strict";
// The blind test seen from the player (web/app.js): the song's frame is blurred under a way
// to the game, the buttons the radio refuses are greyed, and the game page's own details.
const test = require("node:test");
const assert = require("node:assert/strict");

const { load, until, STATUS } = require("./harness");

const PLAYING = {
  state: "playing", round: 2, rounds: 5, clip_sec: 20, error: null, answered: 0, mine: null,
  choices: ["A - 1", "B - 2", "C - 3", "D - 4"], remaining: 12, owner: false,
  round_choices: [5, 10], second_choices: [10, 20],
  scores: [{ name: "Fox", points: 1, me: true }, { name: "Owl", points: 3, me: false }],
};

test("during a game the song's frame is blurred under a way to the game", async () => {
  const page = load({ hash: "#home/player", routes: {
    "GET /api/status": Object.assign({}, STATUS, { mode: "game" }),
    "GET /api/game": PLAYING,
  } });
  const cover = await until(() => !page.$("gameCover").hidden && page.$("gameCover"));
  assert.ok(page.document.querySelector(".now-playing").classList.contains("is-game"));
  assert.equal(page.$("btnPause").disabled, true, "the radio refuses Pause during a game");
  assert.equal(page.$("btnNext").disabled, true);
  assert.equal(page.document.querySelector("#npNotices").textContent.includes("Blind test"), false,
               "the frame says it, not a notice under the buttons");
  cover.querySelector("#gameCoverGo").click();
  await until(() => page.window.location.hash === "#home/game");
  await page.close();
});

test("the game page: one point is singular, the voice options are the owner's", async () => {
  const page = load({ hash: "#home/game", routes: {
    "GET /api/status": Object.assign({}, STATUS, { mode: "game" }),
    "GET /api/game": PLAYING,
  } });
  await until(() => page.document.querySelectorAll("#gameScores li").length === 2);
  const points = [...page.document.querySelectorAll("#gameScores .game-points")].map((p) => p.textContent);
  assert.deepEqual(points, ["1 pt", "3 pts"]);
  assert.equal(page.$("gameVoiceForm").hidden, true, "a guest does not see the host's options");
  await page.close();
});

test("before the first round everyone chooses to play or watch, the host may start early", async () => {
  const joining = Object.assign({}, PLAYING, { state: "joining", round: 0, choices: [], role: null,
    players: 1, spectators: 0, undecided: 2, join_left: 30, owner: true, scores: [] });
  const page = load({ hash: "#home/game", routes: {
    "GET /api/status": STATUS,
    "GET /api/game": joining,
    "POST /api/game/join": (request) => Object.assign({}, joining, { role: request.body.play ? "player" : "spectator" }),
    "POST /api/game/go": {},
  } });
  await until(() => !page.$("gameRole").hidden);
  assert.equal(page.$("gameRound").textContent, "The game starts in 30 s");
  assert.match(page.$("gameRoleCount").textContent, /Players: 1 .* Still choosing: 2/);
  assert.equal(page.$("gameGoRow").hidden, false, "the host may start without waiting");
  assert.equal(page.$("gameVoiceForm").hidden, true, "the game's settings wait for it to be over");
  page.$("gamePlayBtn").click();
  await until(() => page.sent("POST", "/api/game/join").length);
  assert.deepEqual(page.sent("POST", "/api/game/join")[0].body, { play: true });
  page.$("gameGoBtn").click();
  await until(() => page.sent("POST", "/api/game/go").length);
  await page.close();
});

test("the hall of fame counts the wins, and a player's wins show by their score", async () => {
  const lobby = { state: "none", owner: true, round_choices: [5], second_choices: [10],
                  hall: [{ name: "Owl", wins: 4 }, { name: "Fox", wins: 1 }] };
  const page = load({ hash: "#home/game", routes: { "GET /api/status": STATUS, "GET /api/game": lobby } });
  await until(() => !page.$("gameHallBox").hidden);
  const rows = [...page.document.querySelectorAll("#gameHall li")].map((li) => li.textContent);
  assert.deepEqual(rows, ["Owl4 wins", "Fox1 win"]);
  assert.equal(page.$("gameHallResetRow").hidden, false, "the host may start it over");
  await page.close();

  const playing = Object.assign({}, PLAYING, { role: "player", hall: lobby.hall,
    scores: [{ name: "Owl", points: 3, me: false, wins: 4 }] });
  const game = load({ hash: "#home/game", routes: { "GET /api/status": STATUS, "GET /api/game": playing } });
  await until(() => game.document.querySelector("#gameScores .game-wins"));
  assert.equal(game.document.querySelector("#gameScores .game-wins").textContent, "4 wins");
  assert.equal(game.$("gameHallBox").hidden, true, "the hall waits for the lobby");
  await game.close();
});

test("a finished game shows its podium: second, first, third", async () => {
  const over = Object.assign({}, PLAYING, { state: "over", owner: true, choices: [], role: "player",
    podium: [{ points: 12, names: ["Fox"] }, { points: 7, names: ["Cat", "Wolf"] }, { points: 1, names: ["Owl"] }] });
  const page = load({ hash: "#home/game", routes: { "GET /api/status": STATUS, "GET /api/game": over } });
  await until(() => !page.$("gamePodium").hidden);
  const places = [...page.document.querySelectorAll("#gamePodium .podium-place")]
    .map((li) => [li.dataset.place, li.querySelector(".podium-names").textContent, li.querySelector(".podium-points").textContent]);
  assert.deepEqual(places, [["1", "Fox", "12 pts"], ["2", "Cat, Wolf", "7 pts"], ["3", "Owl", "1 pt"]]);
  await page.close();
});

test("with the suggestion box off, the page is only about one's name", async () => {
  const page = load({ hash: "#home/suggest", routes: {
    "GET /api/status": STATUS,
    "GET /api/suggestions": { enabled: false, owner: false, text_max: 200, items: [],
                              me: { name: "Fox", rename_wait: 0, locked: false, linked: 0 } },
  } });
  await until(() => page.document.querySelector("#suggestTitle [data-i18n]").textContent === "My name");
  assert.equal(page.$("suggestForm").hidden, true, "nothing to suggest");
  assert.equal(page.$("suggestEmpty").hidden, true);
  assert.equal(page.$("suggestMeLine").hidden, false, "the name and its links stay");
  await page.close();
});

test("aloud: the host's form, the names to mark after the answer, and one hall at a time", async () => {
  const lobby = { state: "none", owner: true, round_choices: [5, 10], second_choices: [10, 20],
                  hall: [{ name: "Owl", wins: 4 }], hall_oral: [{ name: "Ana", wins: 2 }] };
  const page = load({ hash: "#home/game", routes: {
    "GET /api/status": STATUS, "GET /api/game": lobby, "POST /api/game/start": {} } });
  await until(() => !page.$("gameStartForm").hidden);
  assert.equal(page.$("gameNamesRow").hidden, true, "the names belong to a game aloud");
  page.$("gameMode").value = "oral";
  page.$("gameMode").dispatchEvent(new page.window.Event("change"));
  assert.equal(page.$("gameNamesRow").hidden, false);
  page.$("gamePace").value = "host";
  page.$("gameNamesNew").value = "Ana";
  page.$("gameNames").querySelector(".duration-add-btn").click();
  page.$("gameNamesNew").value = "Bo";
  page.$("gameStartForm").dispatchEvent(new page.window.Event("submit", { cancelable: true }));
  await until(() => page.sent("POST", "/api/game/start").length);
  const sent = page.sent("POST", "/api/game/start")[0].body;
  assert.deepEqual([sent.mode, sent.pace, sent.names], ["oral", "host", ["Ana", "Bo"]],
                   "a name typed and not added yet counts too");
  await until(() => !page.$("gameHallBox").hidden);
  assert.equal(page.$("gameHallKind").hidden, false, "both halls: a choice, not both lists");
  assert.equal(page.document.querySelectorAll("#gameHall li").length, 1);
  await page.close();

  const reveal = { state: "reveal", mode: "oral", pace: "host", owner: true, round: 2, rounds: 5, choices: [],
    answer_label: "Fly - Hilary Duff", scores: [], oral_names: [{ name: "Ana", marked: true, first: true },
    { name: "Bo", marked: false, first: false }], round_choices: [5], second_choices: [10] };
  const host = load({ hash: "#home/game", routes: {
    "GET /api/status": Object.assign({}, STATUS, { mode: "game" }), "GET /api/game": reveal,
    "POST /api/game/mark": reveal, "POST /api/game/next": {} } });
  await until(() => !host.$("gameMarks").hidden);
  assert.equal(host.$("gameResult").textContent, "Fly - Hilary Duff");
  assert.equal(host.$("gameRole").hidden, true, "nobody chooses to play or watch aloud");
  const marks = [...host.$("gameMarks").children];
  assert.deepEqual(marks.map((b) => b.textContent), ["Ana+2", "Bo"]);
  marks[1].click();
  await until(() => host.sent("POST", "/api/game/mark").length);
  assert.deepEqual(host.sent("POST", "/api/game/mark")[0].body, { name: "Bo", on: true });
  assert.equal(host.$("gameNextBtn").textContent, "Next round");
  host.$("gameNextBtn").click();
  await until(() => host.sent("POST", "/api/game/next").length);
  await host.close();
});
