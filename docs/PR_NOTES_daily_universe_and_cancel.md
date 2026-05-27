# Branch notes

## Included

- Updated configurable price range.
- Added daily refresh/cache settings for the universe loader.
- Added FinanceDataReader-first loader with local fallback.
- Added cancellation path when an entry request is not completed within the wait window.
- Added confirmation wait before the forced cleanup path continues.
- Improved mock behavior for local dry-run checks.

## Remaining checks

- Verify the official Kiwoom field names for live responses.
- Run dry-run and tests locally before using non-mock mode.
