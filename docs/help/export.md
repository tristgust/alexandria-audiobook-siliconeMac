---
schema_version: 1
slug: export
title: Validate and build the audiobook
summary: Review publication metadata, chapters, output format, and final validation before building.
version: "1.1"
context_ids: ["export", "publication-build", "final-validation"]
destinations: ["export"]
related: ["produce", "maintenance"]
---
# Validate and build the audiobook

Export assembles the finished publication. Review title, author, narrator or cast credit, cover, chapters, output filename, selected format, output location, and final validation.

## Build safely

Build Audiobook submits the exact reviewed plan and dependency fingerprints. Alexandria builds selected outputs in temporary files, validates them, and only then replaces the canonical output atomically.

A failed build does not report Built and does not replace the previous valid output. Stale, missing, failed, blocked, hash-invalid, or required-unreviewed audio blocks the transaction.

## Output choices

The normal choices are M4B audiobook, MP3 audio file, Audacity project package, and Separate chapter files. The displayed filename follows the selected format. Technical details remain collapsed unless troubleshooting requires them.

## After building

The final output loads in the persistent player. A later failed rebuild preserves the previous valid publication and records the failure for Maintenance rather than treating unknown bytes as current.
