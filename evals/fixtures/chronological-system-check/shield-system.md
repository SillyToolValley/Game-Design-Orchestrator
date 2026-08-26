# Breakwater Shield — Wave Contract

## Base sequence

- The shield is initialized to 100 before wave 1.
- The shield loses 18 when each wave is cleared.
- After the wave-clear loss resolves, waves 2 and 4 grant a 12-point repair reward.
- The next wave begins only after the prior wave's loss and reward have both resolved.
- The HUD samples `wave_start_shield` when the next wave begins.
- Shield values are not clamped during these five waves.

## Authored tutorial

At the start of wave 5, the tutorial card says:

> Your shield must be below 40 now. Divert power from weapons before the breach.

The intended lesson is that the player must react immediately because the authored sequence guarantees the stated threshold.

## Low-assistance fallback

The fallback uses the same initialization, damage, update order, and tutorial. It removes only the repair reward after wave 4; the reward after wave 2 remains active.
