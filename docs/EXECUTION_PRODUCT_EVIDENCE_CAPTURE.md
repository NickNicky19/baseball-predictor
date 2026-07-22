# Account-visible execution-product evidence

This lane records what is genuinely visible to the user's account without
placing an entry. It is research-only and cannot prove acceptance, fill,
settlement, return, profitability, or betting authorization.

## Allowed capture

- A user-supplied screenshot from the signed-in product, or an export/API that
  the product expressly permits.
- Retain the original bytes and SHA-256 before transcription.
- Redact account balance, email, phone, address, payment details, and unrelated
  personal information. Preserve the product, timestamp, game, player, line,
  side, price/multiplier, limits, and full payout context.
- Never automate page scraping unless the product expressly authorizes it.
- Do not click Submit and do not place an entry for this evidence stage.

## Required common evidence

- Product and account-visible timestamp.
- Harris County, Texas location context and whether product geolocation passed.
- Provider event identity plus resolved `mlb_game_pk` and game-identity hash.
- Player display plus resolved MLB `player_id` and player-identity hash.
- Hits line and explicit side.
- Submit button visibility, while retaining `entry_submitted=false`.
- Raw screenshot/export path and hash.
- The exact current product rules-observation hash.

## Product-specific evidence

- **Onyx:** currency, redemption value, odds/multiplier, minimum, maximum,
  hypothetical risk, and displayed payout.
- **Novig:** displayed bid, ask, and available liquidity. A displayed quote is
  never labeled a match; requested/matched fields remain null.
- **Chalkboard:** every leg in the complete lineup, every leg multiplier, lineup
  type, risk, total multiplier, potential payout, and correlation constraints.
- **PrizePicks:** every leg in the complete Power/Flex lineup, projection type,
  every leg multiplier or adjustment, total payout, and DNP/Reboot/tie context.

Chalkboard and PrizePicks cannot be evaluated from a single isolated Hits leg:
the complete lineup determines payout and void/reversion behavior.
