"use strict";

const byId = (id) => document.getElementById(id);
const statusLabel = byId("status");
const chart = byId("chart");
const results = byId("results");
const playButton = byId("play");
const demoMessage = byId("demo-message");
const statusLabels = {
  waiting: "等待客户端",
  running: "正在接收排序步骤",
  complete: "三种算法验证通过",
  failed: "有算法验证失败",
  error: "本轮连接中断",
};
const resultLabels = {
  pending: "待运行",
  running: "进行中",
  complete: "验证通过",
  failed: "验证失败",
  interrupted: "已中断",
};
const operationLabels = { compare: "比较位置", swap: "交换位置" };
const idleLabels = {
  waiting: "可在网页播放，或从另一台设备连接",
  running: "准备接收排序步骤",
  complete: "已按升序排列",
  failed: "请查看算法验证结果",
};

let state = null;
let requestingDemo = false;
let bars = [];

function showDemoMessage(message) {
  demoMessage.textContent = message;
  demoMessage.hidden = !message;
}

function updatePlayButton() {
  playButton.disabled = !state || requestingDemo || state.demo_active || state.status === "running";
  if (requestingDemo || state?.demo_active) {
    playButton.textContent = "正在播放…";
  } else {
    playButton.textContent = state?.client ? "重新播放" : "播放排序动画";
  }
}

async function startDemo() {
  requestingDemo = true;
  updatePlayButton();
  showDemoMessage("");
  try {
    const response = await fetch("/api/demo", { method: "POST" });
    if (response.status !== 202 && response.status !== 409) {
      throw new Error("演示请求失败");
    }
    const latest = await response.json();
    if (!state || latest.version >= state.version) applyState(latest);
    if (response.status === 409) showDemoMessage("已有排序在运行，请稍候。");
  } catch {
    showDemoMessage("无法启动演示，请确认服务端在运行后重试。");
  } finally {
    requestingDemo = false;
    updatePlayButton();
  }
}

function makeBars(values) {
  chart.replaceChildren();
  chart.classList.toggle("empty", values.length === 0);
  chart.style.minWidth = values.length ? (values.length * 40) + "px" : "";
  if (!values.length) chart.textContent = "空列表";
  return values.map(() => {
    const slot = document.createElement("div");
    slot.className = "slot";
    const bar = document.createElement("div");
    const value = document.createElement("span");
    value.className = "bar-value";
    bar.append(value);
    slot.append(bar);
    chart.append(slot);
    return { bar, value };
  });
}

function renderChart(state) {
  const values = state.values;
  chart.setAttribute("aria-label", "当前列表 " + (values.length ? values.join("、") : "为空"));
  if (bars.length !== values.length || !chart.hasChildNodes()) bars = makeBars(values);
  const minimum = Math.min(0, ...state.original, ...values);
  const maximum = Math.max(1, ...state.original, ...values);
  const range = maximum - minimum;
  chart.style.setProperty("--zero", (-minimum / range * 100) + "%");
  const verified = state.current && state.results[state.current].verified === true;

  values.forEach((number, index) => {
    const { bar, value } = bars[index];
    const active = state.operation?.indices.includes(index);
    const appearance = active ? state.operation.kind : verified ? "sorted" : "";
    bar.className = ["bar", number < 0 ? "negative" : "", appearance].filter(Boolean).join(" ");
    bar.style.setProperty("--bottom", ((Math.min(0, number) - minimum) / range * 100) + "%");
    bar.style.setProperty("--height", (Math.abs(number) / range * 100) + "%");
    value.textContent = number;
  });
}

function renderResults(state) {
  const rows = Object.entries(state.algorithms).map(([key, label]) => {
    const result = state.results[key];
    const status = state.status === "error" && result.status === "running"
      ? "interrupted" : result.status;
    const counts = status === "pending"
      ? ["—", "—", "—"] : [result.comparisons, result.swaps, result.steps];
    const duration = result.elapsed_ms === null
      ? "—" : result.elapsed_ms.toLocaleString("zh-CN", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
      });
    const row = document.createElement("tr");
    row.dataset.result = status;
    const heading = document.createElement("th");
    heading.scope = "row";
    heading.textContent = label;
    row.append(heading);
    for (const metric of [...counts, duration, resultLabels[status]]) {
      row.insertCell().textContent = metric;
    }
    row.lastElementChild.dataset.result = status;
    return row;
  });
  results.replaceChildren(...rows);
}

function render(state) {
  if (statusLabel.dataset.status !== state.status || !statusLabel.dataset.loaded) {
    statusLabel.textContent = statusLabels[state.status];
    statusLabel.dataset.status = state.status;
    statusLabel.dataset.loaded = "true";
  }
  byId("server-address").textContent = state.server || "—";
  byId("client-address").textContent = state.client || "等待连接";
  byId("chart-title").textContent = state.current ? state.algorithms[state.current] : "原始列表";
  const original = "[" + state.original.join(", ") + "]";
  byId("sequence").textContent = state.current ? "原始列表 " + original : original;
  byId("step-count").textContent = (state.current ? state.results[state.current].steps : 0) + " 步";

  const operation = byId("operation");
  if (state.error) {
    operation.textContent = state.error;
    operation.dataset.operation = "error";
  } else if (state.operation) {
    const [left, right] = state.operation.indices;
    operation.textContent = operationLabels[state.operation.kind] + " " + (left + 1) + " 和 " + (right + 1);
    operation.dataset.operation = state.operation.kind;
  } else if (state.demo_active && state.status !== "running") {
    operation.textContent = "正在启动本机 TCP 客户端";
    operation.dataset.operation = "idle";
  } else {
    operation.textContent = idleLabels[state.status] || "";
    operation.dataset.operation = "idle";
  }
  renderChart(state);
  renderResults(state);
}

function applyState(latest) {
  if (state?.version === latest.version) return;
  state = latest;
  render(state);
  updatePlayButton();
  showDemoMessage(state.demo_error || "");
}

async function poll() {
  let interval = 100;
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error("状态接口返回错误");
    applyState(await response.json());
  } catch {
    statusLabel.textContent = "正在重新连接服务端";
    statusLabel.dataset.status = "error";
    delete statusLabel.dataset.loaded;
    state = null;
    updatePlayButton();
    interval = 600;
  }
  setTimeout(poll, interval);
}

playButton.addEventListener("click", startDemo);
poll();
