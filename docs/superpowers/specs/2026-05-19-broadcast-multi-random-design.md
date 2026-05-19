# Broadcast Multi Random Design

## Goal

Add a broadcast text mode that lets admins choose between one saved text and randomized saved texts per target, while keeping group list saving tolerant of invalid or expired Telegram targets.

## Broadcast Flow

After an admin selects a group list from Broadcast, the bot shows three choices:

- `Single Text`: choose one saved text and use it for every target, matching the current saved-text behavior.
- `Multi Random`: use all saved texts owned by the admin. For each target/entity send attempt, pick one saved text at random.
- `New message`: enter a fresh message and optionally save it, matching the current manual-message behavior.

If no saved texts exist, `Single Text` and `Multi Random` should not proceed; the bot should tell the admin to save text first.

## Sending Behavior

`Multi Random` randomizes per target/entity send, not per round. In one broadcast round, different groups may receive different saved texts. The next round randomizes again.

Watermark behavior remains unchanged: for free users, the watermark is appended to whichever text is selected for that send.

Media behavior remains unchanged for manual messages. Saved texts remain text-only because the current saved text storage only persists text content and a media flag, not reusable media bytes.

## Manage Group Behavior

Group list create/add continues to parse only Telegram-like targets from pasted text: usernames, t.me links, invite links, addlist links, and numeric chat IDs.

The bot does not validate whether those targets are joinable during save. Invalid, expired, banned, or inaccessible Telegram-like targets are still saved. Validation happens only during broadcast, where failures are logged and the broadcast continues.

## Current Join Behavior

Broadcast currently attempts to join/resolve each target during each broadcast pass. This remains unchanged so accounts that are not yet joined can attempt to join when the broadcast runs.

## Testing

Add or update tests around pure helper/flow logic where available. At minimum verify manually that:

- Broadcast shows `Single Text`, `Multi Random`, and `New message` after choosing a group list.
- `Single Text` still sends the selected saved text.
- `Multi Random` uses the admin's saved texts and chooses per target/entity send.
- Empty saved text state blocks saved-text modes with a clear message.
- Manage Group save/add keeps Telegram-like invalid-looking links without trying to join them.
