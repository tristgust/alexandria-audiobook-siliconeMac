'use strict';

// Compatibility entry point. The maintained Phase 18C behavior harness is
// phase18c_roster_ui_harness.js. Preserve support for the earlier positional
// repository-root argument while keeping one implementation of the tests.
if (
  process.argv[2]
  && !process.argv.includes('--repo-root')
) {
  process.argv.splice(2, 0, '--repo-root');
}

require('./phase18c_roster_ui_harness.js');
