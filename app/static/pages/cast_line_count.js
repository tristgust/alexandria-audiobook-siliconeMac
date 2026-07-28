'use strict';

export function castScriptLineCount(character = {}) {
  const candidates = [
    character.script_connection?.script_line_count,
    character.character?.expanded?.script_line_count,
    character.script_line_count,
    character.line_count,
  ];
  for (const candidate of candidates) {
    if (candidate === '' || candidate === null || candidate === undefined) continue;
    const count = Number(candidate);
    if (Number.isFinite(count) && count >= 0) return count;
  }
  return 0;
}
