"use strict";
// The page's script as the web server serves it (src/web_bundle.py): web/js/boot.js, then
// every web/js/init/*.js in name order as the body of initApp(). Run on its own, it checks
// that each part reads as code where it lands, and that the whole does.
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

function appScript() {
  const texts = parts().map((file) => fs.readFileSync(file, "utf8"));
  return texts[0] + OPEN + texts.slice(1).join("") + CLOSE;
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
