'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPORT_PREFIX = 'VOICE_ALIAS_UI_REPORT=';

function scanToMatching(source, openIndex, openChar, closeChar) {
  let depth = 0;
  let quote = null;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;

  for (let index = openIndex; index < source.length; index += 1) {
    const char = source[index];
    const next = source[index + 1];
    if (lineComment) {
      if (char === '\n') lineComment = false;
      continue;
    }
    if (blockComment) {
      if (char === '*' && next === '/') {
        blockComment = false;
        index += 1;
      }
      continue;
    }
    if (quote) {
      if (escaped) {
        escaped = false;
      } else if (char === '\\') {
        escaped = true;
      } else if (char === quote) {
        quote = null;
      }
      continue;
    }
    if (char === '/' && next === '/') {
      lineComment = true;
      index += 1;
      continue;
    }
    if (char === '/' && next === '*') {
      blockComment = true;
      index += 1;
      continue;
    }
    if (char === '\'' || char === '"' || char === '`') {
      quote = char;
      continue;
    }
    if (char === openChar) depth += 1;
    if (char === closeChar) {
      depth -= 1;
      if (depth === 0) return index;
    }
  }
  throw new Error(`Unbalanced ${openChar}${closeChar} at ${openIndex}`);
}

function extractFunction(source, name) {
  const pattern = new RegExp(`(?:async\\s+)?function\\s+${name}\\s*\\(`);
  const match = pattern.exec(source);
  if (!match) throw new Error(`Function not found: ${name}`);
  const start = match.index;
  const brace = source.indexOf('{', start);
  const end = scanToMatching(source, brace, '{', '}');
  return source.slice(start, end + 1);
}

function check(report, name, condition, details = {}) {
  report.checks[name] = { ok: Boolean(condition), ...details };
  if (!condition) {
    throw new Error(`Voice alias UI check failed: ${name}: ${JSON.stringify(details)}`);
  }
}

function field(value = '') {
  return {
    value,
    textContent: '',
    dataset: {},
    disabled: false,
  };
}

function main() {
  const repoIndex = process.argv.indexOf('--repo-root');
  if (repoIndex < 0 || !process.argv[repoIndex + 1]) {
    throw new Error('--repo-root is required');
  }
  const root = path.resolve(process.argv[repoIndex + 1]);
  const source = fs.readFileSync(
    path.join(root, 'app', 'static', 'index.html'),
    'utf8',
  );
  const report = { checks: {} };

  let aliasTypeQueries = 0;
  const aliasCard = {
    dataset: { voice: 'MARCUS' },
    querySelector(selector) {
      if (selector === '.alias-select') return field('NARRATOR');
      if (selector === '.voice-type:checked') {
        aliasTypeQueries += 1;
        return field('clone');
      }
      throw new Error(`Alias card queried dormant selector: ${selector}`);
    },
  };
  const independentCard = {
    dataset: { voice: 'NARRATOR', savedCloneBackend: 'qwen3_base' },
    querySelector(selector) {
      const values = {
        '.alias-select': field(''),
        '.voice-type:checked': field('custom'),
        '.voice-select': field('Aiden'),
        '.character-style': field('Measured narration.'),
      };
      if (!(selector in values)) {
        throw new Error(`Independent card selector unavailable: ${selector}`);
      }
      return values[selector];
    },
  };

  const summary = field();
  const sourceLabel = field();
  const targetField = field();
  const typeField = field();
  const sourceField = field();
  const chainField = field();
  const editButton = field();
  const diagnosticCard = {
    dataset: { voice: 'MARCUS' },
    querySelector(selector) {
      const values = {
        '.voice-panel-summary-meta': summary,
        '.voice-panel-source': sourceLabel,
        '[data-alias-resolved-target]': targetField,
        '[data-alias-resolved-type]': typeField,
        '[data-alias-resolved-source]': sourceField,
        '[data-alias-chain]': chainField,
        '.alias-edit-target': editButton,
      };
      return values[selector] || null;
    },
  };

  const context = {
    console,
    window: { _loraModelsCache: [] },
    document: {
      querySelectorAll(selector) {
        if (selector !== '.voice-card') return [];
        return context.mode === 'collect'
          ? [aliasCard, independentCard]
          : [diagnosticCard];
      },
    },
    mode: 'collect',
  };
  vm.createContext(context);
  const names = [
    'voiceTypeLabel',
    'collectVoiceConfigForCard',
    'collectVoiceConfig',
    'voiceCardByName',
    'applyAliasDiagnostics',
  ];
  const extracted = names.map((name) => extractFunction(source, name));
  vm.runInContext(extracted.join('\n\n'), context, {
    filename: 'index.html#voice-alias-functions',
  });

  const collected = context.collectVoiceConfig();
  check(
    report,
    'actual_source_extraction',
    extracted.length === names.length,
    { names },
  );
  check(
    report,
    'alias_posts_only_alias_field',
    JSON.stringify(collected.MARCUS) === JSON.stringify({ alias_of: 'NARRATOR' }),
    { collected: collected.MARCUS },
  );
  check(
    report,
    'alias_does_not_read_dormant_controls',
    aliasTypeQueries === 0,
    { aliasTypeQueries },
  );
  check(
    report,
    'independent_update_explicitly_clears_alias',
    collected.NARRATOR.alias_of === null
      && collected.NARRATOR.type === 'custom'
      && collected.NARRATOR.voice === 'Aiden'
      && collected.NARRATOR.character_style === 'Measured narration.',
    { collected: collected.NARRATOR },
  );

  context.mode = 'diagnostics';
  context.applyAliasDiagnostics({
    MARCUS: {
      is_alias: true,
      alias_of: 'NARRATOR',
      chain: ['MARCUS', 'NARRATOR'],
      resolved_target: 'NARRATOR',
      resolved_type: 'custom',
      resolved_source: 'Serena',
    },
  });
  check(
    report,
    'target_diagnostics_propagate_to_live_alias_summary',
    summary.textContent === 'Inherits NARRATOR'
      && sourceLabel.textContent === 'Inherited · Standard voice'
      && targetField.textContent === 'NARRATOR'
      && typeField.textContent === 'Standard voice'
      && sourceField.textContent === 'Serena'
      && chainField.textContent === 'MARCUS → NARRATOR'
      && editButton.dataset.aliasEditTarget === 'NARRATOR'
      && editButton.textContent === 'Edit NARRATOR'
      && editButton.disabled === false,
    {
      summary: summary.textContent,
      sourceLabel: sourceLabel.textContent,
      target: targetField.textContent,
      type: typeField.textContent,
      source: sourceField.textContent,
      chain: chainField.textContent,
      editTarget: editButton.dataset.aliasEditTarget,
    },
  );

  process.stdout.write(`${REPORT_PREFIX}${JSON.stringify(report)}\n`);
}

try {
  main();
} catch (error) {
  console.error(error.stack || String(error));
  process.exitCode = 1;
}
