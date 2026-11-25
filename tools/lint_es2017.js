// Check the app JS parses as ES2017 (iOS 12).  node tools/lint_es2017.js docs/js/*.js docs/sw.js
const acorn = require("/usr/share/nodejs/acorn");
const fs = require("fs"); let bad = 0;
for (const f of process.argv.slice(2)) {
  try { acorn.parse(fs.readFileSync(f, "utf8"), {ecmaVersion: 2017, sourceType: "script"}); console.log("OK", f); }
  catch (e) { bad = 1; console.log("FAIL", f, e.message); }
}
process.exit(bad);
