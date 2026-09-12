(function () {
  "use strict";

  var CSRF_TOKEN = document
    .querySelector('meta[name="todo-csrf-token"]')
    .getAttribute("content");

  var errorBanner = document.getElementById("error-banner");
  var todoListEl = document.getElementById("todo-list");
  var addForm = document.getElementById("add-form");

  var filterSearch = document.getElementById("filter-search");
  var filterStatus = document.getElementById("filter-status");
  var filterTag = document.getElementById("filter-tag");
  var filterSort = document.getElementById("filter-sort");

  function showError(message) {
    errorBanner.textContent = message;
    errorBanner.hidden = false;
  }

  function clearError() {
    errorBanner.hidden = true;
    errorBanner.textContent = "";
  }

  function apiRequest(method, path, body) {
    var options = {
      method: method,
      headers: {},
    };
    if (method !== "GET") {
      options.headers["X-Todo-Token"] = CSRF_TOKEN;
    }
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch(path, options).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) {
          throw new Error(data.error || "エラー: 通信に失敗しました");
        }
        return data;
      });
    });
  }

  function buildQuery() {
    var params = new URLSearchParams();
    if (filterSearch.value) params.set("search", filterSearch.value);
    if (filterStatus.value) params.set("status", filterStatus.value);
    if (filterTag.value) params.set("tag", filterTag.value);
    if (filterSort.value) params.set("sort", filterSort.value);
    var query = params.toString();
    return query ? "/api/todos?" + query : "/api/todos";
  }

  function parseTags(text) {
    return text
      .split(",")
      .map(function (t) {
        return t.trim();
      })
      .filter(function (t) {
        return t !== "";
      });
  }

  function formatTags(tags) {
    return (tags || []).join(", ");
  }

  function renderRow(item) {
    var tr = document.createElement("tr");

    var doneTd = document.createElement("td");
    var checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = item.done;
    checkbox.addEventListener("change", function () {
      apiRequest("PATCH", "/api/todos/" + item.id, { done: checkbox.checked })
        .then(function () {
          clearError();
          refresh();
        })
        .catch(function (err) {
          checkbox.checked = !checkbox.checked;
          showError(err.message);
        });
    });
    doneTd.appendChild(checkbox);
    tr.appendChild(doneTd);

    var idTd = document.createElement("td");
    idTd.textContent = "#" + item.id;
    tr.appendChild(idTd);

    var textTd = document.createElement("td");
    textTd.textContent = item.text;
    tr.appendChild(textTd);

    var dueTd = document.createElement("td");
    dueTd.textContent = item.due || "";
    tr.appendChild(dueTd);

    var priorityTd = document.createElement("td");
    priorityTd.textContent = item.priority || "";
    tr.appendChild(priorityTd);

    var tagsTd = document.createElement("td");
    tagsTd.textContent = formatTags(item.tags);
    tr.appendChild(tagsTd);

    var editTd = document.createElement("td");
    var editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.textContent = "編集";
    editBtn.addEventListener("click", function () {
      renderEditRow(tr, item);
    });
    editTd.appendChild(editBtn);
    tr.appendChild(editTd);

    var rmTd = document.createElement("td");
    var rmBtn = document.createElement("button");
    rmBtn.type = "button";
    rmBtn.textContent = "削除";
    rmBtn.addEventListener("click", function () {
      apiRequest("DELETE", "/api/todos/" + item.id)
        .then(function () {
          clearError();
          refresh();
        })
        .catch(function (err) {
          showError(err.message);
        });
    });
    rmTd.appendChild(rmBtn);
    tr.appendChild(rmTd);

    return tr;
  }

  function renderEditRow(tr, item) {
    tr.innerHTML = "";

    tr.appendChild(document.createElement("td"));
    var idTd = document.createElement("td");
    idTd.textContent = "#" + item.id;
    tr.appendChild(idTd);

    var textTd = document.createElement("td");
    var textInput = document.createElement("input");
    textInput.type = "text";
    textInput.value = item.text;
    textTd.appendChild(textInput);
    tr.appendChild(textTd);

    var dueTd = document.createElement("td");
    var dueInput = document.createElement("input");
    dueInput.type = "date";
    dueInput.value = item.due || "";
    dueTd.appendChild(dueInput);
    tr.appendChild(dueTd);

    var priorityTd = document.createElement("td");
    var prioritySelect = document.createElement("select");
    [
      ["", "(未設定)"],
      ["high", "high"],
      ["medium", "medium"],
      ["low", "low"],
    ].forEach(function (pair) {
      var option = document.createElement("option");
      option.value = pair[0];
      option.textContent = pair[1];
      prioritySelect.appendChild(option);
    });
    prioritySelect.value = item.priority || "";
    priorityTd.appendChild(prioritySelect);
    tr.appendChild(priorityTd);

    var tagsTd = document.createElement("td");
    var tagsInput = document.createElement("input");
    tagsInput.type = "text";
    tagsInput.value = formatTags(item.tags);
    tagsTd.appendChild(tagsInput);
    tr.appendChild(tagsTd);

    var saveTd = document.createElement("td");
    var saveBtn = document.createElement("button");
    saveBtn.type = "button";
    saveBtn.textContent = "保存";
    saveBtn.addEventListener("click", function () {
      var body = {
        text: textInput.value,
        due: dueInput.value === "" ? null : dueInput.value,
        priority: prioritySelect.value === "" ? null : prioritySelect.value,
        tags: parseTags(tagsInput.value),
      };
      apiRequest("PATCH", "/api/todos/" + item.id, body)
        .then(function () {
          clearError();
          refresh();
        })
        .catch(function (err) {
          showError(err.message);
        });
    });
    saveTd.appendChild(saveBtn);
    tr.appendChild(saveTd);

    var cancelTd = document.createElement("td");
    var cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.textContent = "キャンセル";
    cancelBtn.addEventListener("click", function () {
      refresh();
    });
    cancelTd.appendChild(cancelBtn);
    tr.appendChild(cancelTd);
  }

  function refresh() {
    apiRequest("GET", buildQuery())
      .then(function (items) {
        todoListEl.innerHTML = "";
        items.forEach(function (item) {
          todoListEl.appendChild(renderRow(item));
        });
      })
      .catch(function (err) {
        showError(err.message);
      });
  }

  addForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var textInput = document.getElementById("add-text");
    var dueInput = document.getElementById("add-due");
    var priorityInput = document.getElementById("add-priority");
    var tagsInput = document.getElementById("add-tags");

    var body = {
      text: textInput.value,
      due: dueInput.value === "" ? null : dueInput.value,
      priority: priorityInput.value === "" ? null : priorityInput.value,
      tags: parseTags(tagsInput.value),
    };

    apiRequest("POST", "/api/todos", body)
      .then(function () {
        clearError();
        textInput.value = "";
        dueInput.value = "";
        priorityInput.value = "";
        tagsInput.value = "";
        refresh();
      })
      .catch(function (err) {
        showError(err.message);
      });
  });

  [filterSearch, filterStatus, filterTag, filterSort].forEach(function (el) {
    el.addEventListener("change", refresh);
  });
  filterSearch.addEventListener("input", refresh);

  filterStatus.value = "pending";
  refresh();
})();
