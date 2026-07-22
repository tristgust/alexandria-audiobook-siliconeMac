---
schema_version: 1
slug: script
title: Review and approve the Script
summary: Resolve source, speaker, and delivery issues before Cast can begin.
version: "1.1"
context_ids: ["script", "script-review", "script-import"]
destinations: ["script"]
related: ["project-home", "cast"]
---
# Review and approve the Script

Script is the authoritative spoken text. Alexandria keeps source wording exact unless an explicit workflow permits a reviewed transformation.

## Resolve review issues

Use the issue filters to move through Uncertain speaker, Delivery direction, and Source mismatch items. The selected inspector shows the complete Script entry and available source comparison.

Apply the selected resolution only when the correction is supported. Keep the current attribution when the proposed change is not justified. Imported or generated results remain review candidates until accepted.

## Approve Script

Approve Script remains disabled while blocking issues are current. Approval validates the source, Script, metadata, and lifecycle fingerprints, records an immutable accepted version, and makes Cast eligible to continue.

## Source integrity

Pronunciation guidance and synthesis settings do not rewrite source or accepted Script wording. Corrections return through the existing local generation, Task Bundle, or imported-Script review workflow; Alexandria does not patch authoritative entries through an unsafe hidden editor.
