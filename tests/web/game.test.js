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
