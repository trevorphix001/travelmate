// Minimal Server-Sent Events parsing for fetch() streams (EventSource cannot POST).

/** Split buffered text into complete SSE frames. Returns {frames, rest}. Pure, unit-testable. */
export function parseFrames(buffer) {
  const text = buffer.replace(/\r\n/g, "\n");
  const parts = text.split("\n\n");
  const rest = parts.pop();
  const frames = [];
  for (const block of parts) {
    let event = "message";
    const data = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue; // comment / heartbeat
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (data.length) frames.push({ event, data: data.join("\n") });
  }
  return { frames, rest };
}

/** POST json and call onEvent(event, parsedData) for each frame. Resolves when the stream ends. */
export async function postStream(url, body, headers, onEvent, signal) {
  const res = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...headers },
    body: JSON.stringify(body), signal,
  });
  if (!res.ok) {
    let problem = { title: `HTTP ${res.status}`, detail: "" };
    try { problem = await res.json(); } catch { /* not JSON */ }
    onEvent("error", problem);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { frames, rest } = parseFrames(buffer);
    buffer = rest;
    for (const f of frames) {
      let data = f.data;
      try { data = JSON.parse(f.data); } catch { /* keep text */ }
      onEvent(f.event, data);
    }
  }
}
