const fileInput = document.querySelector("#program");
const runButton = document.querySelector("#run");
const uart = document.querySelector("#uart");
const log = document.querySelector("#log");
const summary = document.querySelector("#summary");
let eventSource = null;

function selectedMode() {
  return document.querySelector('input[name="mode"]:checked').value;
}

function append(target, text) {
  target.textContent += text;
  target.scrollTop = target.scrollHeight;
}

function updateMode() {
  const source = selectedMode() === "source";
  fileInput.accept = source ? ".c,.s,.S" : ".bin";
  document.querySelector("#file-prompt").textContent = source
    ? "Choose a .c or .S file"
    : "Choose a raw little-endian .bin file";
  runButton.textContent = source ? "Compile and run" : "Load and run";
  fileInput.value = "";
}

function updateStatus(data) {
  document.querySelector("#core-run").textContent = data.core_run ? "1" : "0";
  document.querySelector("#exit-bits").textContent = data.exit_bits.toString(2).padStart(2, "0");
  document.querySelector("#lock").textContent = data.lock ? "1" : "0";
  document.querySelector("#done").textContent = data.done ? "1" : "0";
}

function showResult(data) {
  const passed = data.state === "passed";
  summary.className = `summary ${passed ? "passed" : "failed"}`;
  summary.textContent = passed
    ? `Passed in ${data.elapsed_seconds.toFixed(3)} s`
    : `Failed: ${data.error || (data.timed_out ? "timeout" : "processor did not finish")}`;
  append(log, JSON.stringify(data, null, 2) + "\n");
  runButton.disabled = false;
}

document.querySelectorAll('input[name="mode"]').forEach((item) => item.addEventListener("change", updateMode));
document.querySelector("#clear").addEventListener("click", () => { uart.textContent = ""; });

runButton.addEventListener("click", async () => {
  if (!fileInput.files.length) {
    summary.textContent = "Choose a program first";
    return;
  }
  if (eventSource) eventSource.close();
  uart.textContent = "";
  log.textContent = "";
  runButton.disabled = true;
  summary.className = "summary running";
  summary.textContent = "Starting…";

  const form = new FormData();
  form.append("mode", selectedMode());
  form.append("timeout", document.querySelector("#timeout").value);
  form.append("program", fileInput.files[0]);
  try {
    const response = await fetch("/api/runs", { method: "POST", body: form });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "Unable to start run");
    summary.textContent = `Run ${body.run_id}`;
    const runId = body.run_id;
    const stream = new EventSource(`/api/runs/${runId}/events`);
    eventSource = stream;
    stream.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (event.type === "uart") append(uart, event.data.text);
      if (event.type === "log") append(log, event.data.message + "\n");
      if (event.type === "compiler") append(log, event.data.text + "\n");
      if (event.type === "status") updateStatus(event.data);
      if (event.type === "result") {
        showResult(event.data);
        stream.close();
        if (eventSource === stream) eventSource = null;
      }
    };
    stream.onerror = async () => {
      if (eventSource !== stream) return;
      stream.close();
      eventSource = null;
      if (!runButton.disabled) return;

      // A terminal run is persisted before the stream closes. Recover it via
      // the ordinary status endpoint so a transport drop cannot hide the real
      // compiler or processor error.
      try {
        const response = await fetch(`/api/runs/${runId}`);
        const saved = await response.json();
        if (response.ok && (saved.state === "passed" || saved.state === "failed")) {
          showResult({ state: saved.state, ...(saved.result || {}) });
          return;
        }
      } catch (_) {
        // Fall through to the connection error below.
      }
      summary.className = "summary failed";
      summary.textContent = "Event stream disconnected before the run finished";
      runButton.disabled = false;
    };
  } catch (error) {
    summary.className = "summary failed";
    summary.textContent = error.message;
    runButton.disabled = false;
  }
});
