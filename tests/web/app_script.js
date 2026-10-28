"use strict";
// The page's script assembled as src/web_bundle.py serves it; run alone, it checks every part parses.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const WEB = path.join(__dirname, "..", "..", "web");
const OPEN = "async function initApp() {\n";
const CLOSE = "} // end of initApp()\n";

function parts() {
  const inner = path.join(WEB, "js", "init");
  const names = fs.readdirSync(inner).filter((name) => name.endsWith(".js")).sort();
  return [path.join(WEB, "js", "boot.js")].concat(names.map((name) => path.join(inner, name)));
}

let assembled = null;

function appScript() {
  // Every test loads the page: the parts are read and joined once per process, not once per test.
  if (assembled === null) {
    const texts = parts().map((file) => fs.readFileSync(file, "utf8"));
    assembled = texts[0] + OPEN + texts.slice(1).join("") + CLOSE;
  }
  return assembled;
}

function check() {
  const [boot, ...inner] = parts();
  const failures = [];
  const tryParse = (file, code) => {
    try {
      new vm.Script(code, { filename: file });
    } catch (e) {
      failures.push(path.relative(WEB, file) + ": " + e.message);
    }
  };
  tryParse(boot, fs.readFileSync(boot, "utf8"));
  // A part ends where a statement of initApp() ends: alone in the function, it still reads.
  inner.forEach((file) => tryParse(file, OPEN + fs.readFileSync(file, "utf8") + CLOSE));
  tryParse(path.join(WEB, "app.js"), appScript());
  return failures;
}

module.exports = { appScript, parts };

if (require.main === module) {
  const failures = check();
  failures.forEach((line) => console.error(line));
  if (failures.length) process.exit(1);
  console.log(parts().length + " parts, " + appScript().split("\n").length + " lines: ok");
}
