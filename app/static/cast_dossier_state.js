'use strict';

export function isCompletedCastPackage(packageSummary) {
  return packageSummary?.activation?.completed === true
    && packageSummary?.status === 'complete';
}
