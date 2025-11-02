# Changelog

## 2025-10-31

### Fixed

- AI subsidy completion flow
  - Summary: Improves how subsidy completion is determined by the AI logic to prevent premature or incorrect completion states.
  - Impact: More reliable completion checks for subsidy-related operations.
  - Meta: 1 file changed, +129 −80 across 4 commits.
  - PR: [#3 Fix ai subsidy completion](https://github.com/Klavrin/FarmXpert-backend/pull/3)
  - Authors: @kynexi
- Eligibility determination logic
  - Summary: Corrects the logic used to evaluate user or entity eligibility, addressing incorrect outcomes under certain conditions.
  - Impact: More accurate eligibility results; reduces false positives/negatives.
  - Meta: 2 files changed, +139 −38 across 4 commits.
  - PR: [#2 Fix eligibility](https://github.com/Klavrin/FarmXpert-backend/pull/2)
  - Authors: @kynexi

## 2025-10-30

### Fixed

- Scraper reliability
  - Summary: Repairs scraper functionality to handle source changes and improve stability.
  - Impact: Restores data ingestion; fewer scraping errors.
  - Meta: 2 files changed, +146 −17 across 5 commits.
  - PR: [#1 Fix scraper](https://github.com/Klavrin/FarmXpert-backend/pull/1)
  - Authors: @kynexi

## Notes

- Contributors this period: @kynexi
- Repository language: 100% Python
