// R-F3659 — semantic lint for the Node tier.
//
// WHY: `npm run lint` (scripts/lint.mjs) is a SYNTAX gate — `node --check` over
// every tracked .mjs. It cannot see an undefined identifier, a dead `else if`,
// or a duplicate `case`. On 2026-08-03 four defects were found that all produce
// perfectly valid JavaScript and were therefore invisible to it:
//
//   R-F3651  handleAriaMention used `rawReply`/`reply` that were never declared
//            — ReferenceError on EVERY WhatsApp @-mention (no-undef)
//   R-F3652  reportOutcome?.() on an undeclared binding — optional call does not
//            guard an undeclared name, it still throws (no-undef)
//   R-F3653  a duplicate `case 'investigate'` made the second handler dead
//            (no-duplicate-case)
//   R-F3654  a suspended->active branch behind a bare `status === 'active'`,
//            so reactivation emails never sent (no-dupe-else-if)
//
// STATUS: REPORTING ONLY. `npm run lint` stays the blocking syntax gate;
// `npm run lint:js` runs this. The tree currently has ~690 findings (mostly
// no-empty / no-unused-vars), so making this blocking today would fail CI on
// day one. Burn the backlog down, then flip it to blocking.
import js from '@eslint/js';
import globals from 'globals';

// Correctness rules only — the ones that indicate code which cannot work.
// Style is deliberately out of scope; a noisy gate gets switched off.
const correctness = {
  'require-atomic-updates': 'error',   // async read-modify-write races
  'no-unsafe-optional-chaining': 'error',
  'no-promise-executor-return': 'error',
  'no-async-promise-executor': 'error',
  'no-unmodified-loop-condition': 'error',
  'no-unreachable-loop': 'error',
  'no-self-compare': 'error',
  'no-template-curly-in-string': 'error',
  'array-callback-return': 'error',
  'no-constant-binary-expression': 'error',
  'no-dupe-else-if': 'error',          // R-F3654
  'no-duplicate-case': 'error',        // R-F3653
  'no-duplicate-imports': 'error',
  'no-eval': 'error',
  'no-implied-eval': 'error',
  'no-new-func': 'error',
  'no-script-url': 'error',
  'no-return-assign': 'error',
  'no-unused-vars': ['error', { args: 'none', varsIgnorePattern: '^_', caughtErrors: 'none' }],
};

export default [
  {
    ignores: [
      'node_modules/**',
      // vendored + minified third-party assets — not our code, and they
      // drown the signal (jquery/bootstrap/popper alone produced 784 findings)
      'public/vendor/**',
      'public/pelican/**',
      'public/js/vendor/**',
      '**/*.min.js',
      // Workflow-tool-persisted bodies legitimately use top-level `return`
      'scripts/workflows/**',
      'awesome-deepseek-agent-main/**',
      '.venv/**',
      'data/**',
      'searxng/**',
      'aria-app/**/dist/**',
    ],
  },
  js.configs.recommended,
  {
    files: ['**/*.mjs', '**/*.js', '**/*.cjs'],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: 'module',
      globals: { ...globals.node },
    },
    rules: correctness,
  },
  {
    files: ['**/*.cjs'],
    languageOptions: { sourceType: 'commonjs' },
  },
  {
    files: ['public/**/*.js', 'dashboard/**/*.js'],
    languageOptions: {
      sourceType: 'script',
      globals: {
        ...globals.browser,
        // Classic multi-script page: public/js/app.js declares these at script
        // top level, so later <script> files legitimately see them. Declaring
        // them keeps no-undef MEANINGFUL — that is the rule that caught the
        // R-F3651/R-F3652 WhatsApp ReferenceErrors, and 25 false positives here
        // would have trained everyone to ignore it.
        API: 'readonly',
        Auth: 'readonly',
        postJson: 'readonly',
        io: 'readonly',          // socket.io client, loaded via <script>
      },
    },
  },
];
