const { exec } = require("child_process");

async function loadResults(query) {
  const res = fetch("/api/search?q=" + query);
  const data = await res.json();
  return data;
}

function renderResults(items) {
  const el = document.getElementById("results");
  el.innerHTML = items.map((i) => "<li>" + i.title + "</li>").join("");
}

function runReport(name) {
  exec("generate-report --name " + name, (err, stdout) => {
    if (err) console.log(err);
    console.log(stdout);
  });
}

function parseConfig(text) {
  return eval("(" + text + ")");
}

module.exports = { loadResults, renderResults, runReport, parseConfig };
