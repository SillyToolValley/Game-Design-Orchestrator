# Ember Relay — Game Design Document

Status: prototype brief

Target: 1–2 players, desktop

Session target: 12 minutes

## Player promise

Carry a living ember through a flooded ruin while deciding whether to spend its heat opening safer routes or preserve it for the final beacon.

## Round flow

Each room is a 90-second round. The exit opens when both pressure plates are held for five continuous seconds. If the timer expires, the flood rises and the team loses one supply crate.

## Match structure

A run contains four rooms and a beacon chamber. Each round lasts 180 seconds so a cautious solo player can inspect every side passage. The HUD countdown begins as soon as the room loads.

## Heat and defeat

The carrier begins with 100 heat. Water and enemy contact reduce heat. At zero heat, that runner is permanently removed for the rest of the expedition and drops their entire inventory into the flood. A solo run ends immediately.

## Checkpoint retry

When a runner reaches zero heat, press `R` to retry instantly from the current room checkpoint. The runner returns with full heat and keeps all keys and carried currency so the player can immediately try a different route.

## Economy

The expedition uses three currencies: coal, signal glass, and salvage tokens.

- Coal restores 20 heat at a brazier and is lost when spent.
- Signal glass permanently tunes one beacon color for the current expedition.

The top HUD shows all three currency icons. Between rooms, the upgrade tray offers a reroll button alongside two ember upgrades.

## Controls

- Move: `WASD` or left stick
- Aim ember: mouse or right stick
- Pass ember: left mouse button or right trigger
- Interact: `E` or south face button
- Retry from checkpoint: `R`

## End state

Lighting the final beacon completes the expedition. The results screen shows time, rooms cleared, currency collected, and the number of retries.
