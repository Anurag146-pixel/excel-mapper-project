let gridData = [];
let annotations = [];
let currentCell = null;

document.getElementById("fileInput").onchange = async (e) => {
  const formData = new FormData();
  formData.append("file", e.target.files[0]);

  const res = await fetch("http://127.0.0.1:5000/upload", {
    method: "POST",
    body: formData
  });

  const data = await res.json();
  gridData = data.grid;
  renderGrid();
};

function renderGrid() {
  const table = document.getElementById("grid");
  table.innerHTML = "";

  gridData.forEach((row, r) => {
    const tr = document.createElement("tr");
    row.forEach((cell, c) => {
      const td = document.createElement("td");
      td.innerText = cell;
      td.oncontextmenu = (e) => showMenu(e, r, c);
      tr.appendChild(td);
    });
    table.appendChild(tr);
  });
}

function showMenu(e, r, c) {
  e.preventDefault();
  currentCell = { r, c };
  const menu = document.getElementById("menu");
  menu.style.top = e.pageY + "px";
  menu.style.left = e.pageX + "px";
  menu.style.display = "block";
}

function tagCell(tag) {
  annotations.push({
    row: currentCell.r,
    col: currentCell.c,
    value: gridData[currentCell.r][currentCell.c],
    tag: tag,
    scope_id: "section_1"
  });
  document.getElementById("menu").style.display = "none";
}

async function compileTemplate() {
  const res = await fetch("http://127.0.0.1:5000/compile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      grid: gridData,
      annotations: annotations
    })
  });

  const data = await res.json();
  document.getElementById("output").innerText = JSON.stringify(data, null, 2);
}
