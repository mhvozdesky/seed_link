"use strict";

const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync(process.argv[2], "utf8");
const actions = JSON.parse(process.argv[3] || "[]");
const dataMatch = html.match(/<script id="seedlink-data"[^>]*>([\s\S]*?)<\/script>/);
const scripts = Array.from(html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/g));
if (!dataMatch || scripts.length < 2) throw new Error("report scripts not found");
const applicationScript = scripts[scripts.length - 1][1];

class Element {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.listeners = {};
    this.textContent = "";
    this.className = "";
    this.value = "";
    this.type = "";
    this.colSpan = 1;
    this.width = 720;
    this.height = 310;
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  append(...children) {
    children.forEach((child) => this.appendChild(child));
  }

  replaceChildren(...children) {
    this.children = [];
    this.append(...children);
  }

  addEventListener(type, listener) {
    (this.listeners[type] ||= []).push(listener);
  }

  dispatch(type) {
    (this.listeners[type] || []).forEach((listener) => listener({target: this}));
  }

  querySelector(selector) {
    if (selector === "thead") return this.thead || null;
    if (selector === "tbody") return this.tbody || null;
    const filter = selector.match(/^\[data-filter="([^"]+)"\]$/);
    if (filter) return (this.filters || []).find((item) => item.dataset.filter === filter[1]) || null;
    return null;
  }

  querySelectorAll(selector) {
    if (selector === "[data-filter]") return (this.filters || []).slice();
    return [];
  }

  getContext() {
    return {
      clearRect() {}, fillRect() {}, fillText() {},
      fillStyle: "", font: "", textBaseline: "", textAlign: ""
    };
  }
}

class DocumentShim {
  constructor(payloadText) {
    this.byId = {};
    this.controls = {};
    this.metrics = {};
    this.tables = {};
    this.resets = {};
    this.body = new Element("body");
    this.register("main", new Element("main"));
    const data = this.register("seedlink-data", new Element("script"));
    data.textContent = payloadText;
    ["hero-meta", "integrity", "overview-cards", "metadata-list"].forEach((id) => this.register(id, new Element("div")));
    ["funnel-chart", "crop-chart", "time-chart", "month-chart"].forEach((id) => this.register(id, new Element("canvas")));

    const sourceTable = this.register("source-table", this.makeTable());
    sourceTable.dataset.kind = "source";
    const filterNames = {
      participants: ["search", "memberType", "memberStatus", "dateFrom", "dateTo"],
      vouchers: ["search", "method", "issues"],
      products: ["search", "crop", "taxId", "voucherKey", "dateFrom", "dateTo"],
      otherProducts: ["search", "taxId", "dateFrom", "dateTo"],
      issues: ["search", "level", "role", "resolved"]
    };
    Object.entries(filterNames).forEach(([area, names]) => {
      const controls = new Element("div");
      controls.dataset.controls = area;
      controls.filters = names.map((name) => {
        const node = new Element(name === "search" || name.startsWith("date") ? "input" : "select");
        node.dataset.filter = name;
        return node;
      });
      this.controls[area] = controls;
      this.metrics[area] = new Element("div");
      this.tables[area] = this.makeTable();
      const reset = new Element("button");
      reset.dataset.reset = area;
      this.resets[area] = reset;
    });
  }

  register(id, element) {
    this.byId[id] = element;
    return element;
  }

  makeTable() {
    const table = new Element("table");
    table.thead = new Element("thead");
    table.tbody = new Element("tbody");
    return table;
  }

  createElement(tagName) {
    return new Element(tagName);
  }

  getElementById(id) {
    return this.byId[id] || null;
  }

  querySelector(selector) {
    if (selector.startsWith("#")) {
      if (selector === "#source-table tbody") return this.byId["source-table"].tbody;
      return this.byId[selector.slice(1)] || null;
    }
    let match = selector.match(/^\[data-controls="([^"]+)"\]$/);
    if (match) return this.controls[match[1]] || null;
    match = selector.match(/^\[data-table="([^"]+)"\]$/);
    if (match) return this.tables[match[1]] || null;
    match = selector.match(/^\[data-metrics="([^"]+)"\]$/);
    if (match) return this.metrics[match[1]] || null;
    match = selector.match(/^\[data-reset="([^"]+)"\]$/);
    if (match) return this.resets[match[1]] || null;
    match = selector.match(/^\[data-controls="([^"]+)"\] \[data-filter="([^"]+)"\]$/);
    if (match) return this.controls[match[1]].querySelector(`[data-filter="${match[2]}"]`);
    return null;
  }

  querySelectorAll(selector) {
    const match = selector.match(/^\[data-controls="([^"]+)"\] \[data-filter\]$/);
    if (match) return this.controls[match[1]].querySelectorAll("[data-filter]");
    return [];
  }
}

const documentShim = new DocumentShim(dataMatch[1]);
global.document = documentShim;
vm.runInThisContext(applicationScript, {filename: "report.js"});

function metrics(area) {
  return Object.fromEntries(documentShim.metrics[area].children.map((item) => [
    item.children[0].textContent,
    item.children[1].textContent
  ]));
}

function overview() {
  return Object.fromEntries(documentShim.byId["overview-cards"].children.map((item) => [
    item.children[0].textContent,
    item.children[1].textContent
  ]));
}

function snapshot() {
  return {
    integrity: documentShim.byId.integrity.textContent,
    fatal: documentShim.byId.main.children[0] && documentShim.byId.main.children[0].children[0]
      ? documentShim.byId.main.children[0].children[0].textContent : null,
    metrics: Object.fromEntries(Object.keys(documentShim.metrics).map((area) => [area, metrics(area)])),
    overview: overview(),
    rows: Object.fromEntries(Object.entries(documentShim.tables).map(([area, table]) => [
      area,
      table.tbody.children.length && table.tbody.children[0].children[0].className === "empty" ? 0 : table.tbody.children.length
    ])),
    tableRows: Object.fromEntries(Object.entries(documentShim.tables).map(([area, table]) => [
      area,
      table.tbody.children.map((row) => row.children.map((cell) => cell.textContent))
    ]))
  };
}

const states = [snapshot()];
actions.forEach((action) => {
  if (action.type === "sort") {
    const heading = documentShim.tables[action.area].thead.children[0];
    const button = heading.children.map((cell) => cell.children[0])
      .find((item) => item.textContent.startsWith(action.column));
    if (!button) throw new Error(`sort column not found: ${action.area}.${action.column}`);
    button.dispatch("click");
  } else {
    const control = documentShim.controls[action.area].querySelector(`[data-filter="${action.filter}"]`);
    control.value = action.value;
    control.dispatch("input");
  }
  states.push(snapshot());
});
process.stdout.write(JSON.stringify(states));
