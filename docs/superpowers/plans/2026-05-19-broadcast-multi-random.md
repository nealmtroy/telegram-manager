# Broadcast Multi Random Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a broadcast mode that lets admins choose single saved text, multi-random saved text, or a new manual message after selecting a group list.

**Architecture:** Keep the existing state-machine flow in `telegram_manager/bot.py`. Add a mode selection callback after group-list selection, store either one selected text or the full saved-text pool in `_state`, and choose random text at the point of each target/entity send.

**Tech Stack:** Python, aiogram callback/message handlers, Telethon send/join flow, existing JSON/Supabase-backed DB helpers.

---

## File Structure

- Modify `telegram_manager/bot.py`
  - `cb_bc`: change from direct saved-text picker to mode picker.
  - Add callbacks for `bm:single` and `bm:multi`.
  - Keep `cb_sm` for single saved-text selection.
  - `_start_broadcast`: support `saved_texts` list and pick `random.choice()` per entity send.
  - Confirmation text in `broadcast_delay_round`: show selected text mode.
- No DB schema change. `get_saved_messages()` already returns all admin saved texts.
- No Manage Group code change is required because current create/add paths already save extracted Telegram-like targets without validating joinability.

---

### Task 1: Add broadcast text mode selection

**Files:**
- Modify: `telegram_manager/bot.py:600-617`

- [ ] **Step 1: Inspect current callback flow**

Read `telegram_manager/bot.py:600-617` and confirm `cb_bc` currently sets `_state[uid] = {"action": "broadcast_msg_choice", "list": list_name}` then immediately renders saved-text buttons and `New message`.

- [ ] **Step 2: Replace `cb_bc` with mode buttons**

Replace the existing `cb_bc` body with this implementation:

```python
@router.callback_query(F.data.startswith("bc:"))
async def cb_bc(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    list_name = cq.data[3:]
    _state[uid] = {"action": "broadcast_mode_choice", "list": list_name}
    buttons = [
        [InlineKeyboardButton(text="Single Text", callback_data="bm:single")],
        [InlineKeyboardButton(text="Multi Random", callback_data="bm:multi")],
        [InlineKeyboardButton(text="New message", callback_data="newmsg")],
    ]
    await cq.message.edit_text(
        f"List: {list_name}\nPilih mode text broadcast:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
```

- [ ] **Step 3: Add single/multi mode callbacks after `cb_bc`**

Insert these callback handlers immediately after `cb_bc`:

```python
@router.callback_query(F.data == "bm:single")
async def cb_bm_single(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    saved = get_saved_messages(uid)
    if not saved:
        await cq.message.edit_text("Belum ada text tersimpan. Simpan text dulu di Kelola Text.")
        _state.pop(uid, None)
        return
    _state[uid]["action"] = "broadcast_msg_choice"
    buttons = [[InlineKeyboardButton(text=s["name"], callback_data=f"sm:{s['name']}")] for s in saved]
    await cq.message.edit_text(
        "Pilih text tersimpan:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "bm:multi")
async def cb_bm_multi(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    saved = get_saved_messages(uid)
    if not saved:
        await cq.message.edit_text("Belum ada text tersimpan. Simpan text dulu di Kelola Text.")
        _state.pop(uid, None)
        return
    _state[uid]["saved_texts"] = [s["text"] for s in saved]
    _state[uid]["text_mode"] = "multi_random"
    _state[uid]["action"] = "broadcast_delay_group"
    buttons = [[InlineKeyboardButton(text="Auto (3-10s)", callback_data="dg:auto"),
                InlineKeyboardButton(text="No delay", callback_data="dg:none")]]
    await cq.message.edit_text(
        f"Multi Random aktif ({len(saved)} text).\nDelay antar group?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
```

- [ ] **Step 4: Compile-check the file**

Run:

```bash
python -m py_compile telegram_manager/bot.py
```

Expected: command exits with status 0 and prints no output.

- [ ] **Step 5: Commit**

Run:

```bash
git add telegram_manager/bot.py docs/superpowers/specs/2026-05-19-broadcast-multi-random-design.md docs/superpowers/plans/2026-05-19-broadcast-multi-random.md
git commit -m "Add broadcast text mode selection"
```

Expected: commit succeeds.

---

### Task 2: Randomize saved text per target/entity send

**Files:**
- Modify: `telegram_manager/bot.py:935-999`

- [ ] **Step 1: Update `_start_broadcast` message preparation**

In `_start_broadcast`, replace the current saved-text/manual-message branch:

```python
    if "saved_text" in st:
        msg_text = st["saved_text"]
    elif "message" in st:
```

with:

```python
    saved_texts = st.get("saved_texts") or []
    if "saved_text" in st:
        msg_text = st["saved_text"]
    elif saved_texts:
        msg_text = ""
    elif "message" in st:
```

Keep the rest of the manual-message branch unchanged.

- [ ] **Step 2: Replace global watermark append with a per-send helper**

Replace this block:

```python
    if watermark:
        msg_text = (msg_text + f"\n\n{watermark}") if msg_text else watermark
```

with:

```python
    def message_text_for_send() -> str:
        selected_text = random.choice(saved_texts) if saved_texts else msg_text
        if watermark:
            return (selected_text + f"\n\n{watermark}") if selected_text else watermark
        return selected_text
```

- [ ] **Step 3: Use the helper inside every entity send**

Inside the `for entity in entities:` loop, replace uses of `msg_text` in `send_file` and `send_message` with a per-entity local value:

```python
                            text_to_send = message_text_for_send()
                            if has_media and media_bytes:
                                await client.send_file(entity, media_bytes, caption=text_to_send, parse_mode="html", file_name=media_filename)
                            else:
                                await client.send_message(entity, text_to_send, parse_mode="html")
```

The surrounding loop should remain:

```python
                        for entity in entities:
                            text_to_send = message_text_for_send()
                            if has_media and media_bytes:
                                await client.send_file(entity, media_bytes, caption=text_to_send, parse_mode="html", file_name=media_filename)
                            else:
                                await client.send_message(entity, text_to_send, parse_mode="html")
                            sent_count += 1
```

- [ ] **Step 4: Compile-check the file**

Run:

```bash
python -m py_compile telegram_manager/bot.py
```

Expected: command exits with status 0 and prints no output.

- [ ] **Step 5: Commit**

Run:

```bash
git add telegram_manager/bot.py
git commit -m "Randomize broadcast saved text per target"
```

Expected: commit succeeds.

---

### Task 3: Update broadcast confirmation text

**Files:**
- Modify: `telegram_manager/bot.py:1488-1503`

- [ ] **Step 1: Add text mode label before confirmation**

In the `broadcast_delay_round` action branch, after `has_media = False`, add:

```python
        if st.get("saved_texts"):
            text_mode = f"multi random ({len(st['saved_texts'])} text)"
        elif st.get("saved_text"):
            text_mode = "single saved text"
        else:
            text_mode = "new message"
```

- [ ] **Step 2: Include text mode in the confirmation message**

In the `await message.answer(` confirmation string, add this line after the `Accounts:` line:

```python
            f"Text mode: {text_mode}\n"
```

The start of the confirmation should become:

```python
        await message.answer(
            f"Broadcasting (continuous)\n"
            f"List: {st['list']} ({len(bl.targets)} targets)\n"
            f"Accounts: {len(accounts)}\n"
            f"Text mode: {text_mode}\n"
            f"Delay per group: {_format_delay(st['group_delay'])}\n"
```

- [ ] **Step 3: Compile-check the file**

Run:

```bash
python -m py_compile telegram_manager/bot.py
```

Expected: command exits with status 0 and prints no output.

- [ ] **Step 4: Commit**

Run:

```bash
git add telegram_manager/bot.py
git commit -m "Show broadcast text mode summary"
```

Expected: commit succeeds.

---

### Task 4: Verify Manage Group saving behavior remains tolerant

**Files:**
- Inspect: `telegram_manager/bot.py:236-269`
- Inspect: `telegram_manager/bot.py:1283-1344`

- [ ] **Step 1: Confirm parsing remains Telegram-like only**

Verify `_extract_group_targets()` still only adds Telegram URL matches, `@username`, invite/addlist-style strings, and numeric chat IDs.

- [ ] **Step 2: Confirm no join validation occurs when saving**

Verify `createlist_targets` and `listadd_targets` only call `_extract_group_targets()`, update `state["targets"]` or `bl.targets`, and call `add_list()`.

There should be no calls to these functions in the save path:

```python
_join_and_resolve_target
_join_and_resolve_chatlist
_broadcast_entities_for_target
```

- [ ] **Step 3: Compile-check the file**

Run:

```bash
python -m py_compile telegram_manager/bot.py
```

Expected: command exits with status 0 and prints no output.

- [ ] **Step 4: Do not commit if no code changed**

If Task 4 only verifies existing behavior and no files changed, do not create an empty commit.

---

### Task 5: Final verification

**Files:**
- Verify: `telegram_manager/bot.py`

- [ ] **Step 1: Check git diff**

Run:

```bash
git diff --stat HEAD~3..HEAD
```

Expected: only `telegram_manager/bot.py`, the design spec, and this plan are included across the work.

- [ ] **Step 2: Compile all Python files**

Run:

```bash
python -m compileall telegram_manager main.py
```

Expected: command exits with status 0. It may print `Compiling ...` lines.

- [ ] **Step 3: Manual bot verification**

Start the bot using the repo's normal command:

```bash
python main.py
```

In Telegram, verify:

1. Open `📣 Broadcast`.
2. Choose a group list.
3. Confirm buttons show `Single Text`, `Multi Random`, and `New message`.
4. Choose `Single Text`, pick a saved text, and confirm the delay flow starts.
5. Stop and repeat with `Multi Random`; confirm it shows `Multi Random aktif (N text)` and reaches the delay flow.
6. Start a small broadcast to a safe test group list and confirm the summary says `Text mode: multi random (N text)`.
7. Send `stop`.

- [ ] **Step 4: Report join behavior clearly**

When reporting completion, state: broadcast still attempts join/resolve during each broadcast pass for each target; failed targets are logged and do not stop the full broadcast.

---

## Self-Review

- Spec coverage: mode selection, single text, multi random using all saved texts, random per target/entity send, empty saved text handling, unchanged manage group save behavior, and unchanged join timing are covered.
- Placeholder scan: no placeholder steps remain.
- Type consistency: state keys are `saved_text`, `saved_texts`, `text_mode`, `message`, `group_delay`, and `round_delay`; all are consistent with existing `_state` usage.
