(function () {
  "use strict";

  try {

  const dataNode = document.getElementById("seedlink-data");
  const report = JSON.parse(dataNode.textContent);
  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const text = (value) => value === null || value === undefined || value === "" ? "—" : String(value);
  const join = (value) => Array.isArray(value) ? (value.length ? value.join("; ") : "—") : text(value);
  const norm = (value) => text(value).toLocaleLowerCase("uk-UA");
  const create = (tag, value, className) => {
    const node = document.createElement(tag);
    if (value !== undefined) node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  const statusLabels = {complete: "повний", partial: "частковий", unavailable: "недоступний"};
  const levelLabels = {
    import_blocking: "Блокує імпорт", record: "Проблема запису",
    unknown_numeric: "Невідомий внесок", internal: "Внутрішня", export: "Експорт"
  };

  const powers = [1n];
  function power10(scale) {
    while (powers.length <= scale) powers.push(powers[powers.length - 1] * 10n);
    return powers[scale];
  }
  function decimal(value) {
    if (value === null || value === undefined) return null;
    const match = /^([+-]?)(\d+)(?:\.(\d*))?$/.exec(String(value));
    if (!match) throw new Error("Некоректне десяткове значення у звіті");
    return {integer: BigInt((match[1] || "") + match[2] + (match[3] || "")), scale: (match[3] || "").length};
  }
  function add(left, right) {
    if (!left) return right;
    if (!right) return left;
    const scale = Math.max(left.scale, right.scale);
    return {
      integer: left.integer * power10(scale - left.scale) + right.integer * power10(scale - right.scale),
      scale: scale
    };
  }
  function sum(values) {
    return values.reduce((total, value) => value === null ? total : add(total, decimal(value)), {integer: 0n, scale: 0});
  }
  function decimalString(number) {
    if (!number) return "0";
    const negative = number.integer < 0n;
    let digits = (negative ? -number.integer : number.integer).toString();
    if (number.scale) digits = digits.padStart(number.scale + 1, "0");
    let result = number.scale ? digits.slice(0, -number.scale) + "." + digits.slice(-number.scale) : digits;
    if (number.scale) result = result.replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
    return (negative ? "-" : "") + result;
  }
  function decimalCompare(left, right) {
    const a = decimal(left || "0");
    const b = decimal(right || "0");
    const scale = Math.max(a.scale, b.scale);
    const av = a.integer * power10(scale - a.scale);
    const bv = b.integer * power10(scale - b.scale);
    return av < bv ? -1 : av > bv ? 1 : 0;
  }
  function roundedDecimalString(value, places) {
    const number = decimal(value);
    if (number.scale <= places) return decimalString(number);
    const divisor = power10(number.scale - places);
    let rounded = number.integer / divisor;
    const remainder = number.integer % divisor;
    const absoluteRemainder = remainder < 0n ? -remainder : remainder;
    if (absoluteRemainder * 2n >= divisor) rounded += number.integer < 0n ? -1n : 1n;
    return decimalString({integer: rounded, scale: places});
  }
  function formatDecimal(value) {
    const normalized = decimalString(decimal(value || "0"));
    const parts = normalized.split(".");
    const grouped = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, " ");
    return parts.length === 2 ? grouped + "," + parts[1] : grouped;
  }
  function quantity(rows, extraUnknown = 0) {
    const knownRows = rows.filter((row) => row.quantity !== null);
    const unknown = rows.reduce(
      (total, row) => total + (Object.prototype.hasOwnProperty.call(row, "unknown") ? row.unknown : Number(row.quantity === null)),
      extraUnknown
    );
    const known = decimalString(sum(knownRows.map((row) => row.quantity)));
    const status = unknown ? (knownRows.length ? "partial" : "unavailable") : "complete";
    return {known, unknown, status};
  }
  function quantityLabel(summary) {
    const base = summary.status === "unavailable" ? "—" : formatDecimal(summary.known);
    return summary.unknown ? base + " + " + summary.unknown + " невід." : base;
  }
  function dateAllowed(value, from, to) {
    if ((from || to) && !value) return false;
    return (!from || value >= from) && (!to || value <= to);
  }
  function boolLabel(value) { return value ? "Так" : "Ні"; }
  function locationsLabel(locations) {
    return locations.map((item) => item.role + " · " + item.sheet + " · рядок " + item.row + " · " + item.field).join("; ");
  }
  function cropsLabel(crops) {
    const labels = {sunflower:"Соняшник", corn:"Кукурудза", other:"Інші культури", unknown:"Не визначено"};
    return Object.keys(labels).filter((key) => crops[key] !== null && decimalCompare(crops[key], "0") !== 0)
      .map((key) => labels[key] + ": " + formatDecimal(crops[key])).join("; ") || "—";
  }

  const columns = {
    participants: [
      ["name", "Учасник", (r) => r.name], ["memberType", "Тип", (r) => r.memberType],
      ["memberStatus", "Статус", (r) => r.memberStatus], ["appearedOn", "Дата появи", (r) => r.appearedOn],
      ["hasActivity", "Є активність", (r) => boolLabel(r.hasActivity)],
      ["hasResult", "Є результат", (r) => boolLabel(r.hasResult)],
      ["hasVoucher", "Є ваучер", (r) => boolLabel(r.hasVoucher)], ["vouchers", "Ваучери", (r) => join(r.vouchers)]
    ],
    vouchers: [
      ["voucher", "Повний ваучер", (r) => r.voucher], ["people", "Учасники", (r) => join(r.people)],
      ["clients", "Клієнти", (r) => join(r.clients)], ["taxIds", "Tax ID", (r) => join(r.taxIds)],
      ["quantity", "Відомий обсяг", (r) => r.quantity === null ? "—" : formatDecimal(r.quantity), "number"],
      ["quantityStatus", "Повнота", (r) => statusLabels[r.quantityStatus]],
      ["unknown", "Невідомі внески", (r) => r.unknown, "number"],
      ["crops", "Культури", (r) => cropsLabel(r.crops), "", (r) => cropsLabel(r.crops)],
      ["referenceDate", "Опорна дата", (r) => r.referenceDate], ["methods", "Методи", (r) => join(r.methods)],
      ["hasIssues", "Проблеми", (r) => r.hasIssues ? "Є невирішені" : "Немає"]
    ],
    products: [
      ["voucher", "Повний ваучер", (r) => r.voucher], ["quantity", "Кількість", (r) => r.quantity === null ? "—" : formatDecimal(r.quantity), "number"],
      ["cropLabel", "Культура", (r) => r.cropLabel], ["hybrid", "Гібрид", (r) => r.hybrid],
      ["taxId", "Tax ID", (r) => r.taxId], ["client", "Клієнт", (r) => r.client],
      ["createdOn", "Дата ProductLine", (r) => r.createdOn], ["referenceDate", "Опорна дата", (r) => r.referenceDate],
      ["timeLabel", "Часова складова", (r) => r.timeLabel]
    ],
    otherProducts: [
      ["voucher", "Повний ваучер", (r) => r.voucher], ["quantity", "Кількість", (r) => r.quantity === null ? "—" : formatDecimal(r.quantity), "number"],
      ["taxId", "Tax ID", (r) => r.taxId], ["client", "Клієнт", (r) => r.client],
      ["cropLabel", "Культура", (r) => r.cropLabel], ["hybrid", "Гібрид", (r) => r.hybrid],
      ["createdOn", "Дата ProductLine", (r) => r.createdOn], ["referenceDate", "Опорна дата", (r) => r.referenceDate],
      ["timeLabel", "Часова складова", (r) => r.timeLabel]
    ],
    issues: [
      ["code", "Код", (r) => r.code], ["level", "Рівень", (r) => levelLabels[r.level] || r.level],
      ["message", "Пояснення", (r) => r.message], ["impacts", "Вплив", (r) => join(r.impacts)],
      ["resolved", "Вирішено", (r) => boolLabel(r.resolved)],
      ["locations", "Джерела", (r) => locationsLabel(r.locations), "", (r) => locationsLabel(r.locations)]
    ]
  };
  const state = Object.fromEntries(Object.keys(columns).map((name) => [name, {sortKey: columns[name][0][0], direction: 1}]));

  function filterValues(area) {
    const controls = q('[data-controls="' + area + '"]');
    const result = {};
    qa("[data-filter]", controls).forEach((node) => { result[node.dataset.filter] = node.value; });
    return result;
  }
  function searchableProduct(row) { return norm([row.voucher, row.client, row.taxId, row.hybrid, row.cropLabel].filter(Boolean).join(" ")); }
  function filtered(area) {
    const values = filterValues(area);
    const search = norm(values.search || "");
    return report[area].filter((row) => {
      if (area === "participants") {
        const haystack = norm([row.name, row.memberType, row.memberStatus, ...row.vouchers].filter(Boolean).join(" "));
        return (!values.search || haystack.includes(search)) && (!values.memberType || row.memberType === values.memberType) &&
          (!values.memberStatus || row.memberStatus === values.memberStatus) && dateAllowed(row.appearedOn, values.dateFrom, values.dateTo);
      }
      if (area === "vouchers") {
        const haystack = norm([row.voucher, ...row.people, ...row.clients, ...row.taxIds].join(" "));
        return (!values.search || haystack.includes(search)) && (!values.method || row.methods.includes(values.method)) &&
          (!values.issues || row.hasIssues === (values.issues === "yes"));
      }
      if (area === "products") {
        return (!values.search || searchableProduct(row).includes(search)) && (!values.crop || row.crop === values.crop) &&
          (!values.taxId || row.taxId === values.taxId) && (!values.voucherKey || row.voucherKey === values.voucherKey) &&
          dateAllowed(row.createdOn, values.dateFrom, values.dateTo);
      }
      if (area === "otherProducts") {
        return (!values.search || searchableProduct(row).includes(search)) && (!values.taxId || row.taxId === values.taxId) &&
          dateAllowed(row.createdOn, values.dateFrom, values.dateTo);
      }
      if (area === "issues") {
        const roles = row.locations.map((item) => item.role);
        const haystack = norm([row.code, row.message, ...row.impacts, locationsLabel(row.locations)].join(" "));
        return (!values.search || haystack.includes(search)) && (!values.level || row.level === values.level) &&
          (!values.role || roles.includes(values.role)) && (!values.resolved || row.resolved === (values.resolved === "yes"));
      }
      return true;
    });
  }
  function compareRows(area, left, right) {
    const key = state[area].sortKey;
    if (key === "quantity") return decimalCompare(left[key], right[key]);
    const column = columns[area].find((item) => item[0] === key);
    const sortable = column[4] || column[2];
    const a = sortable(left);
    const b = sortable(right);
    if (typeof a === "boolean" || typeof b === "boolean") return Number(a) - Number(b);
    return text(a).localeCompare(text(b), "uk", {numeric: true, sensitivity: "base"});
  }
  function renderTable(area, rows) {
    const table = q('[data-table="' + area + '"]');
    const head = q("thead", table);
    const body = q("tbody", table);
    head.replaceChildren(); body.replaceChildren();
    const headingRow = create("tr");
    columns[area].forEach((column) => {
      const th = create("th");
      const arrow = state[area].sortKey === column[0] ? (state[area].direction > 0 ? " ▲" : " ▼") : "";
      const button = create("button", column[1] + arrow);
      button.type = "button";
      button.addEventListener("click", () => {
        if (state[area].sortKey === column[0]) state[area].direction *= -1;
        else { state[area].sortKey = column[0]; state[area].direction = 1; }
        renderArea(area);
      });
      th.appendChild(button); headingRow.appendChild(th);
    });
    head.appendChild(headingRow);
    if (!rows.length) {
      const row = create("tr"); const cell = create("td", "За цими фільтрами рядків немає.", "empty");
      cell.colSpan = columns[area].length; row.appendChild(cell); body.appendChild(row); return;
    }
    rows.forEach((item) => {
      const row = create("tr");
      columns[area].forEach((column) => row.appendChild(create("td", text(column[2](item)), column[3] || "")));
      body.appendChild(row);
    });
  }
  function miniMetric(label, value) {
    const node = create("div", undefined, "mini-metric");
    node.appendChild(create("span", label, "label")); node.appendChild(create("span", value, "value")); return node;
  }
  function productExtraUnknown(values) {
    if (values.taxId || values.dateFrom || values.dateTo || (values.crop && values.crop !== "unknown")) return 0;
    if (values.voucherKey) return report.productExtraUnknownByVoucher[values.voucherKey] || 0;
    return Object.values(report.productExtraUnknownByVoucher).reduce((total, value) => total + value, 0);
  }
  function renderMetrics(area, rows) {
    const box = q('[data-metrics="' + area + '"]'); box.replaceChildren();
    if (area === "participants") {
      const shown = (key, value) => {
        const unknown = report.controls.participants[key].unknown;
        return String(value) + (unknown ? " + " + unknown + " невід." : "");
      };
      box.append(miniMetric("Учасники", shown("participants", rows.length)),
        miniMetric("Є активність", shown("activity", rows.filter((r) => r.hasActivity).length)),
        miniMetric("Є результат", shown("result", rows.filter((r) => r.hasResult).length)),
        miniMetric("Є ваучер", shown("voucher", rows.filter((r) => r.hasVoucher).length)),
        miniMetric("Повнота", report.controls.participants.participants.unknown ? "часткова" : "повна"));
    } else if (area === "vouchers") {
      const total = quantity(rows);
      box.append(miniMetric("Ваучери", String(rows.length)), miniMetric("Клієнти", String(new Set(rows.flatMap((r) => r.taxIds)).size)),
        miniMetric("Відомий обсяг", quantityLabel(total)), miniMetric("Повнота", statusLabels[total.status]));
    } else if (area === "products" || area === "otherProducts") {
      const values = filterValues(area);
      const total = quantity(rows, area === "products" ? productExtraUnknown(values) : 0);
      box.append(miniMetric("Рядки", String(rows.length)), miniMetric("Ваучери", String(new Set(rows.map((r) => r.voucherKey)).size)),
        miniMetric("Клієнти", String(new Set(rows.map((r) => r.taxId).filter(Boolean)).size)), miniMetric("Відомий обсяг", quantityLabel(total)),
        miniMetric("Повнота", statusLabels[total.status]));
    } else if (area === "issues") {
      box.append(miniMetric("Проблеми", String(rows.length)), miniMetric("Невирішені", String(rows.filter((r) => !r.resolved).length)));
    }
  }
  function renderArea(area) {
    const rows = filtered(area).slice().sort((a, b) => state[area].direction * compareRows(area, a, b));
    renderMetrics(area, rows); renderTable(area, rows);
  }

  function addOptions(area, filter, values, labeler = (value) => value) {
    const select = q('[data-controls="' + area + '"] [data-filter="' + filter + '"]');
    Array.from(new Set(values.filter((value) => value !== null && value !== ""))).sort((a, b) => text(labeler(a)).localeCompare(text(labeler(b)), "uk", {numeric: true})).forEach((value) => {
      const option = create("option", labeler(value)); option.value = value; select.appendChild(option);
    });
  }
  function initializeFilters() {
    addOptions("participants", "memberType", report.participants.map((r) => r.memberType));
    addOptions("participants", "memberStatus", report.participants.map((r) => r.memberStatus));
    addOptions("vouchers", "method", report.vouchers.flatMap((r) => r.methods));
    addOptions("products", "crop", report.products.map((r) => r.crop), (value) => ({sunflower:"Соняшник", corn:"Кукурудза", other:"Інші культури", unknown:"Культура не визначена"})[value]);
    addOptions("products", "taxId", report.products.map((r) => r.taxId));
    const voucherNames = Object.fromEntries(report.products.map((r) => [r.voucherKey, r.voucher]));
    addOptions("products", "voucherKey", report.products.map((r) => r.voucherKey), (value) => voucherNames[value]);
    addOptions("otherProducts", "taxId", report.otherProducts.map((r) => r.taxId));
    addOptions("issues", "level", report.issues.map((r) => r.level), (value) => levelLabels[value] || value);
    addOptions("issues", "role", report.issues.flatMap((r) => r.locations.map((item) => item.role)));
    Object.keys(columns).forEach((area) => {
      qa('[data-controls="' + area + '"] [data-filter]').forEach((node) => node.addEventListener("input", () => renderArea(area)));
      q('[data-reset="' + area + '"]').addEventListener("click", () => {
        qa('[data-controls="' + area + '"] [data-filter]').forEach((node) => { node.value = ""; }); renderArea(area);
      });
    });
  }

  function metricCard(measure) {
    const card = create("article", undefined, "metric-card");
    card.appendChild(create("div", measure.label, "label"));
    const displayValue = measure.unit === "%" && measure.known !== null
      ? roundedDecimalString(measure.known, 1) : measure.known;
    const shown = displayValue === null ? "—" : formatDecimal(displayValue) + " " + measure.unit;
    card.appendChild(create("div", shown, "value"));
    const status = create("span", statusLabels[measure.status] + (measure.unknown ? " · невідомих: " + measure.unknown : ""), "status " + measure.status);
    card.appendChild(status); return card;
  }
  function renderOverview() {
    const wanted = ["main.quantity", "main.vouchers", "main.clients", "main.time.before_lead", "main.time.on_or_after_lead",
      "main.time.unknown", "funnel.participants.count", "funnel.voucher.count", "events.surveys", "events.activities",
      "other.quantity", "quality.unresolved_issues", "quality.lead_refs.linked_rate", "quality.activities.linked_rate"];
    const byKey = Object.fromEntries(report.overview.measures.map((item) => [item.key, item]));
    const box = q("#overview-cards"); wanted.forEach((key) => { if (byKey[key]) box.appendChild(metricCard(byKey[key])); });
  }
  function drawBars(canvas, items, color) {
    const context = canvas.getContext("2d"); const width = canvas.width; const height = canvas.height;
    context.clearRect(0, 0, width, height); context.font = "14px Segoe UI, Arial"; context.textBaseline = "middle";
    if (!items.length) { context.fillStyle = "#607089"; context.fillText("Немає даних", 24, height / 2); return; }
    const max = Math.max(...items.map((item) => item.value), 1); const left = 190; const right = 58; const top = 16;
    const band = (height - top * 2) / items.length; const barHeight = Math.min(34, band * .58);
    items.forEach((item, index) => {
      const y = top + band * index + band / 2; const barWidth = Math.max(0, (width - left - right) * item.value / max);
      context.fillStyle = "#607089"; context.textAlign = "right"; context.fillText(item.label, left - 12, y);
      context.fillStyle = color; context.fillRect(left, y - barHeight / 2, barWidth, barHeight);
      context.fillStyle = "#17233c"; context.textAlign = "left"; context.fillText(item.display, Math.min(left + barWidth + 8, width - right + 5), y);
    });
  }
  function renderCharts() {
    const funnel = report.controls.participants;
    drawBars(q("#funnel-chart"), [
      ["participants", "Учасники"], ["activity", "Є активність"], ["result", "Є результат"], ["voucher", "Є ваучер"]
    ].map(([key, label]) => ({label, value: Number(funnel[key].known || 0), display: funnel[key].known || "0"})), "#2366a8");
    const cropLabels = {sunflower:"Соняшник", corn:"Кукурудза", other:"Інші культури", unknown:"Не визначено"};
    const cropTotals = {};
    report.products.forEach((row) => { if (row.quantity !== null) cropTotals[row.crop] = add(cropTotals[row.crop] || {integer:0n,scale:0}, decimal(row.quantity)); });
    drawBars(q("#crop-chart"), Object.keys(cropLabels).map((key) => {
      const value = decimalString(cropTotals[key] || {integer:0n,scale:0});
      return {label: cropLabels[key], value: Number(value), display: formatDecimal(value)};
    }), "#2d8b75");
    const timeLabels = {before_lead:"До появи ліда", on_or_after_lead:"У день/після", unknown:"Час невідомий"};
    const timeTotals = {};
    report.products.forEach((row) => { if (row.quantity !== null) timeTotals[row.timeBucket] = add(timeTotals[row.timeBucket] || {integer:0n,scale:0}, decimal(row.quantity)); });
    drawBars(q("#time-chart"), Object.keys(timeLabels).map((key) => {
      const value = decimalString(timeTotals[key] || {integer:0n,scale:0});
      return {label: timeLabels[key], value: Number(value), display: formatDecimal(value)};
    }), "#7b5ead");
    const monthTotals = {};
    report.products.forEach((row) => {
      if (row.quantity === null) return;
      const month = row.createdOn ? row.createdOn.slice(0, 7) : "Дата невідома";
      monthTotals[month] = add(monthTotals[month] || {integer:0n,scale:0}, decimal(row.quantity));
    });
    drawBars(q("#month-chart"), Object.keys(monthTotals).sort().map((month) => {
      const value = decimalString(monthTotals[month]);
      return {label: month, value: Number(value), display: formatDecimal(value)};
    }), "#c4752d");
  }

  function appendPair(root, label, value) {
    const wrapper = create("div"); wrapper.append(create("dt", label), create("dd", text(value))); root.appendChild(wrapper);
  }
  function renderMetadata() {
    q("#hero-meta").append(create("p", "Ревізія " + report.meta.revision), create("p", "Розрахунок: " + report.meta.calculationId), create("p", "Створено: " + report.meta.exportedAt));
    const list = q("#metadata-list");
    [["Calculation ID", report.meta.calculationId], ["Snapshot ID", report.meta.snapshotId], ["Ревізія", report.meta.revision],
      ["Версія програми", report.meta.programVersion], ["Час розрахунку", report.meta.calculatedAt], ["Час експорту", report.meta.exportedAt],
      ["Комплект входів", report.meta.inputComplete ? "повний" : "неповний"]].forEach((item) => appendPair(list, item[0], item[1]));
    const body = q("#source-table tbody");
    report.meta.sources.forEach((source) => {
      const row = create("tr"); [source.label, source.file, source.sheet, source.schema, source.rows, source.sha256].forEach((value) => row.appendChild(create("td", text(value)))); body.appendChild(row);
    });
  }
  function verifyControls() {
    const problems = [];
    const productTotal = quantity(report.products, Object.values(report.productExtraUnknownByVoucher).reduce((a, b) => a + b, 0));
    const otherTotal = quantity(report.otherProducts); const voucherTotal = quantity(report.vouchers);
    [["товарні рядки", productTotal, report.controls.products.quantity], ["інші ваучери", otherTotal, report.controls.otherProducts.quantity],
      ["ваучери", voucherTotal, report.controls.vouchers.quantity]].forEach(([label, actual, expected]) => {
      if (decimalCompare(actual.known, expected.known || "0") !== 0 || actual.unknown !== expected.unknown) problems.push(label);
    });
    const participantChecks = {participants: report.participants.length, activity: report.participants.filter((r) => r.hasActivity).length,
      result: report.participants.filter((r) => r.hasResult).length, voucher: report.participants.filter((r) => r.hasVoucher).length};
    Object.entries(participantChecks).forEach(([key, value]) => { if (String(value) !== report.controls.participants[key].known) problems.push("воронка: " + key); });
    if (report.vouchers.length !== report.controls.vouchers.vouchers || new Set(report.vouchers.flatMap((r) => r.taxIds)).size !== report.controls.vouchers.clients) problems.push("ваучери: унікальні підсумки");
    const uniqueCountsMatch = (rows, control) => rows.length === control.rows && new Set(rows.map((r) => r.voucherKey)).size === control.vouchers &&
      new Set(rows.map((r) => r.taxId).filter(Boolean)).size === control.clients;
    if (!uniqueCountsMatch(report.products, report.controls.products) || !uniqueCountsMatch(report.otherProducts, report.controls.otherProducts)) problems.push("товарні рядки: унікальні підсумки");
    if (report.issues.length !== report.controls.issues.rows || report.issues.filter((r) => !r.resolved).length !== report.controls.issues.unresolved) problems.push("проблеми");
    const node = q("#integrity"); node.className = "integrity " + (problems.length ? "error" : "ok");
    node.textContent = problems.length ? "Контрольні підсумки не збігаються: " + problems.join(", ") : "Контрольні підсумки збігаються";
  }

  initializeFilters();
  Object.keys(columns).forEach(renderArea);
  renderOverview(); renderCharts(); renderMetadata(); verifyControls();
  } catch (error) {
    const target = document.getElementById("main") || document.body;
    const notice = document.createElement("section");
    notice.className = "report-section";
    const title = document.createElement("h2");
    title.textContent = "Не вдалося відобразити звіт";
    const message = document.createElement("p");
    message.textContent = "Структура або дані HTML-файлу пошкоджені чи несумісні. Створіть звіт повторно. Технічна причина: " + (error && error.message ? error.message : "невідома помилка");
    notice.append(title, message);
    target.replaceChildren(notice);
  }
}());
