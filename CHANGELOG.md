# Changelog

## 2025-11-02

### API reference

- Adds a concise markdown listing of the most important HTTP endpoints, with an example request body and handler locations.
  This results in clearer developer docs.
- Commit: https://github.com/Klavrin/FarmXpert-backend/commit/c9ce2bc883e8ff95cb06d5594d3139a94f4361d5
- Authors: @kynexi

## 2025-10-31

### Improve AI subsidy completion flow

- Improves how subsidy completion is determined by the AI logic to prevent premature or incorrect completion states. This results in more reliable completion checks for subsidy-related operations.
- PR: [#3 Fix ai subsidy completion](https://github.com/Klavrin/FarmXpert-backend/pull/3)
- Authors: @kynexi

### Fix eligibility determination logic

- Corrects the logic used to evaluate user or entity eligibility, addressing incorrect outcomes under certain conditions. This results in more accurate eligibility results; reduces false positives/negatives.
- PR: [#2 Fix eligibility](https://github.com/Klavrin/FarmXpert-backend/pull/2)
- Authors: @kynexi

## 2025-10-30

### Fix Scraper reliability

- Repairs scraper functionality to handle source changes and improve stability. Resulting in fewer scraping errors.
- PR: [#1 Fix scraper](https://github.com/Klavrin/FarmXpert-backend/pull/1)
- Authors: @kynexi

## Notes

- Contributors this period: @kynexi
- Repository language: 100% Python
