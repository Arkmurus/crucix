/**
 * Return true when an SSE transport chunk contains an answer-content event.
 *
 * Python's json.dumps emits spaces by default, while JSON.stringify does not.
 * Parse complete SSE data lines instead of coupling delivery health to either
 * serializer's whitespace. The tolerant regex fallback covers non-canonical
 * but complete JSON data carried in one transport read.
 */
export function containsAnswerChunk(chunk) {
  const text = String(chunk || '');
  for (const line of text.split(/\r?\n/)) {
    if (!line.startsWith('data:')) continue;
    const payload = line.slice(5).trim();
    if (!payload || payload === '[DONE]') continue;
    try {
      const event = JSON.parse(payload);
      if (event && event.type === 'chunk' && typeof event.text === 'string' && event.text.length > 0) {
        return true;
      }
    } catch {
      // A network read may end in the middle of an SSE event. The tolerant
      // fallback below detects the event type without claiming empty text.
    }
  }
  return /"type"\s*:\s*"chunk"/.test(text) && /"text"\s*:\s*"[^"]+/.test(text);
}
