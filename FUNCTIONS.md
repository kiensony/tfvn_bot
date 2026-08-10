# TFVN Bot — Function Catalog

This document lists every user-facing command and background feature the bot currently implements. Command availability depends on which cogs are loaded (`ENVIRONMENT=production` loads all public modules under `cogs/`; development uses `dev_cogs.txt`) and on `DISABLED_COGS`.

**Default prefix:** `!tf ` (space after the token; configurable via `COMMAND_PREFIX`).

Examples below use that default. Replace with your configured prefix if different.

**Access notes:**

| Label | Meaning |
| --- | --- |
| Everyone | Any member who can use bot commands in the channel |
| Booster | Server booster (has premium subscription status) |
| Moderator | Requires the listed Discord permission(s) |
| Administrator | Requires Administrator (and any listed extra permission) |
| NSFW channel | Must be used in a Discord NSFW-marked channel |
| Beta | Requires a role listed in Mongo `BETA_ROLE_IDS` |
| Channel-bound | Only works in a configured game/feature channel |

---

## Help & discovery

| Command | Access | Description |
| --- | --- | --- |
| `help [topic]` | Everyone | Replies to the invoking message with the full bot catalog, covering commands and automatic features even in partial development profiles. Individual command availability still depends on loaded cogs/configuration; only the NSFW topic is channel-gated |
| `mod` | Everyone | Opens the same menu focused on moderation; each listed command enforces its own Discord permission |
| `nsfw` | NSFW channel | Opens the same help menu focused on the NSFW topic; outside NSFW channels the bot warns and deletes the prompt |

---

## General

| Command | Access | Description |
| --- | --- | --- |
| `hello` | Everyone | Greets the invoking user |
| `invite` | Everyone | Replies with the bot invite link (`INVITE_LINK` env) |
| `verify` | Everyone | Points the user to the verification channel (`VERIFY_CHANNEL` env) |
| `ping` | Everyone | Heartbeat / liveness reply |
| `beta_preview` | Beta | Confirms the member has a configured Beta role |
| `server_stats` | Administrator | In-memory uptime, command, and error counts since process start; 10s per-guild cooldown |
| `leave` | Administrator | Makes the bot leave the current guild |
| `setup` / `diagnose` | Manage Guild (subcommands) | Setup diagnostics group |
| `setup check` | Manage Guild | Checks database, permissions, IDs, and cog health |

**Module:** `cogs.general`, `cogs.operation.*`

---

## Triggered replies

Guild administrators can configure persistent, case-insensitive automatic replies. `contains` matches a phrase anywhere in a message; `exact` requires the entire normalized message to match. Replies cannot generate mentions.

Rules are stored in plaintext and shown to administrators by `triggerreply list`; an `exact` phrase can behave like a secret code but should not be used as an authentication secret.

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `triggerreply` | `autoreply` | Administrator | Show the command guide |
| `triggerreply add <contains\|include\|exact> <phrase> \| <reply>` | `autoreply add` | Administrator | Add a contains/exact rule |
| `triggerreply list` | `autoreply list` | Administrator | List configured rules and their numeric IDs |
| `triggerreply remove <ID>` | `triggerreply delete`, `autoreply remove`, `autoreply delete` | Administrator | Delete a configured rule |

Examples:

```text
!tf triggerreply add contains dit me vnpt | vnpt nhu con cac
!tf triggerreply add exact [A SECRET CODE] | something
```

**Module:** `cogs.interaction.triggered_reply`

---

## AFK reminders

| Command | Access | Description |
| --- | --- | --- |
| `afk` | Everyone | Shows AFK subcommand guide |
| `afk dynamic [reason]` | Everyone | AFK until the user posts a message again |
| `afk time` | Everyone | Interactive timed AFK setup |
| `afk clear` | Everyone | Clear active AFK early |
| `afk check` | Everyone | List unread pings received while AFK |

**Background (`afk_monitor`):** detects mentions of AFK members, stores pings, and clears dynamic AFK when the user returns.

**Module:** `cogs.afk_remind.*`

---

## Announcements (automatic)

No user commands. Event listeners only:

| Event | Behavior |
| --- | --- |
| Member join | Welcome embed in `JOIN_CHANNEL` (rules / role channel pointers) |
| Member leave | Goodbye announcement |
| Member ban | Ban announcement |

**Module:** `cogs.announcement.*`

---

## Content of the day

| Command | Access | Description |
| --- | --- | --- |
| `random_femboy` | Everyone | Random image from the `images` collection (`image_collection: femboy`) with optional social metadata |

**Module:** `cogs.cotd.random_femboy`

---

## Economy — Trap Coins

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `daily` | — | Everyone | Once per UTC day; grants **10** Trap Coins |
| `user_balance` | `balance` | Everyone | Show current Trap Coin balance |
| `user_transactions` | `transactions` | Everyone | Last 10 transaction log entries |
| `add_tc @member <amount> [reason]` | `give_tc`, `grant_tc` | Administrator | Credit a non-bot member 1–1,000,000,000 Trap Coins; logs `admin_add_tc` |
| `remove_tc @member <amount> [reason]` | `sub_tc`, `subtract_tc`, `take_tc` | Administrator | Debit a non-bot member 1–1,000,000,000 Trap Coins if the balance is sufficient; logs `admin_remove_tc` |
| `set_tc @member <amount> [reason]` | `set_balance` | Administrator | Set a non-bot member’s balance to 0–1,000,000,000; logs delta as `admin_set_tc` |
| `check_tc [@member]` | `tc_balance` | Administrator | Inspect a non-bot member’s Trap Coin balance (default: author) |

### Shop

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `shop` | `store` | Everyone | List enabled catalog items |
| `shop buy <item_id>` | — | Everyone | Purchase a catalog item; 2 calls per 5 seconds per user |
| `shop inventory [@member]` | `inv` | Everyone | View owned shop items |
| `shop use <item_id>` | — | Everyone | Equip badge or apply purchased role |
| `shop unequip` | — | Everyone | Clear active badge |
| `shop add_role <id> <price> @role [description]` | — | Manage Guild | Add/update a sellable role priced 1–1,000,000,000 TC |
| `shop add_badge <id> <price> <display name>` | — | Manage Guild | Add/update a badge item priced 1–1,000,000,000 TC |
| `shop remove <item_id>` | `disable` | Manage Guild | Hide an item from the shop |

Shop item IDs are 1–32 lowercase letters, digits, `_`, or `-`, and must start with a letter or digit.

**Module:** `cogs.daily_reward.*`, `cogs.economy.shop`

---

## Minigames

| Command | Access | Description |
| --- | --- | --- |
| `slot` | Everyone | Slot machine; costs **5** Trap Coins; logs debit transaction |
| `flip_coin <head\|tail> <n>` | Everyone | Coin flip bet of `n` Trap Coins (needs ≥5 TC to play); win pays 2× stake |
| `sicbo_start` | Everyone | Reaction-based Sic Bo round (Big / Small / Triple); payout wiring is incomplete |
| `noitu` | Channel-bound | Word-chain rules embed |
| `noitu status` | Channel-bound | Current word and used-word list |
| `noitu hint` | Channel-bound | Hint for the chain |
| `noitu end` | Channel-bound | End the current game |
| `noitu analyze` | Channel-bound | Analyze connectivity of the current word |
| `vtv` | Channel-bound | Vua Tiếng Việt rules + current scramble |
| `vtv status` | Channel-bound | Current puzzle status |
| `vtv next` | Channel-bound | Start a new letter-scramble round |
| `vtv hint` | Channel-bound | Reveal a letter hint |

**Background:** `noitu` and `vtv` also accept plain messages in their configured channels for gameplay.

**Module:** `cogs.minigames.*`

---

## Fun meters & cards

Most meters accept an optional `@member` (default: author). Scores are deterministic/daily-style fun output with staged loading where implemented.

| Command | Aliases | Description |
| --- | --- | --- |
| `gay` | — | Gay meter |
| `les` | `les_meter`, `lesbian` | Lesbian meter |
| `ship @user1 @user2` | — | Compatibility meter (two members required) |
| `penisize` | `peni`, `peni_size`, `ppsize` | Size meter |
| `aura` | — | Signed aura score (−/+) |
| `redflag` | `flags` | Red/green flag score (−10 green → +10 red) |
| `based` | — | Based meter |
| `brainrot` | — | Brainrot meter |
| `clown` | — | Clown meter |
| `cope` | — | Cope / seethe / mald meter |
| `cringe` | — | Cringe meter |
| `delulu` | — | Delulu meter |
| `gyatt` | — | Gyatt meter |
| `ick` | — | Ick meter |
| `mainchar` | — | Main-character energy |
| `npc` | — | NPC meter |
| `ohio` | — | Ohio meter |
| `rizz` | — | Rizz meter |
| `simp` | — | Simp meter |
| `skillissue` | — | Skill-issue meter |
| `touchgrass` | — | Touch-grass meter |
| `yapper` | — | Yap meter |
| `femboycard` | — | Personal femboy card from configured role names (`data/femboy_role.txt`) |
| `birthday` | — | Birthday command guide |
| `birthday set <day> <month>` | — | Register birthday (announced by scheduled task) |

**Module:** `cogs.funny_things.*`

---

## Social interactions

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `kiss @user` | — | Everyone | Kiss interaction + GIF; increments stats |
| `hug @user` | — | Everyone | Hug |
| `pat @user` | — | Everyone | Headpat |
| `slap @user` | — | Everyone | Slap |
| `punch @user` | — | Everyone | Punch |
| `hit @user` | — | Everyone | Hit |
| `poke @user` | — | Everyone | Poke |
| `cuddle @user` | — | Everyone | Cuddle |
| `snuggle @user` | — | Everyone | Snuggle |
| `boop @user` | — | Everyone | Boop nose |
| `handhold @user` | `holdhand` | Everyone | Hold hands |
| `bonk @user` | — | Everyone | Bonk |
| `bite @user` | `nom` | Everyone | Bite |
| `stare @user` | — | Everyone | Stare |
| `lick @user` | — | Everyone | Lick |
| `smack @user` | — | Everyone | Affectionate punch (đấm yêu) |
| `avatar [@user]` | `av`, `global_avatar`, `globalav` | Everyone | Show Discord global avatar (default: author) |
| `server_avatar [@user]` | `sav`, `guild_avatar`, `serverav` | Everyone (guild) | Show server avatar, or global avatar if unset |
| `propose @user` | — | Everyone (guild) | Propose marriage; partner presses Yes/No (5m); expired proposals update UI) |
| `marriage [@user]` | `marry`, `marriage_status` | Everyone (guild) | Marriage status embed (rank, level, XP, anniversary) |
| `marriage help` | — | Everyone (guild) | Rules: XP, ranks, cooldowns |
| `marriage top` | `lb`, `leaderboard`, `rank` | Everyone (guild) | Top 10 couples by XP |
| `divorce` | — | Everyone (guild) | End active marriage after confirm buttons |
| `rank [r] [action]` | `ranking` | Everyone | All-time bot-wide interaction leaderboards (`r` = receivers); action can be kiss, hug, pat, slap, punch, hit, poke, cuddle, snuggle, boop, handhold, bonk, bite, stare, lick, or smack |
| `cat` | — | Everyone | Random cat image (external API) |
| `dog` | — | Everyone | Random dog image (external API) |
| `36` | — | Everyone | Static meme GIF reply |

**Module:** `cogs.interaction.user_interaction`, `cat`, `dog`, `meme_interaction`

All 16 SFW interactions require a non-bot target and have a 3-second per-command, per-user cooldown. Self-target is allowed only for `pat`, `slap`, `punch`, `hit`, `poke`, `bonk`, and `smack`.

---

## NSFW features

> Adult content. Restrict to age-gated communities and NSFW channels. Follow Discord ToS and third-party API terms.

### Search

| Command | Access | Description |
| --- | --- | --- |
| `r34 <tags>` | NSFW channel | Rule34 image/video search |
| `gbr <tags>` | NSFW channel | Gelbooru image/video search |

### Interactions (NSFW channel, 3-second per-command, per-user cooldown)

| Command | Aliases | Description |
| --- | --- | --- |
| `nsfwrule` | — | Interaction rules embed |
| `bj @user` | — | Blowjob interaction |
| `rj @user` | — | Rimjob interaction |
| `hj @user` | — | Handjob (self allowed) |
| `fj @user` | — | Footjob interaction |
| `aj @user` | `assjob` | Assjob interaction |
| `tj @user` | `thighjob` | Thighjob interaction |
| `spank @user` | — | Ass spanking (self allowed) |
| `frot @user` | — | Frotting |
| `fuck @user` | — | Sex interaction |
| `cream @user` | — | Creampie |
| `3some @user1 @user2` | `threesome` | Threesome with two others |
| `orgy @user1 … @userN` | — | Orgy with 2–10 other members |
| `ranknsfw [r] [action]` | `nsfwrank` | Current-UTC-month bot-wide leaderboards; action can be bj, rj, hj, fj, aj, tj, spank, frot, fuck, cream, 3some, or orgy |
| `mrank <month> <year>` | — | Administrator monthly NSFW ranking |

### Super-user controls

| Command | Access | Description |
| --- | --- | --- |
| `locknsfw @user` | Configured Queen role | Lock a member's NSFW interaction commands for 24 hours; successful use has a 3-day cooldown |
| `unlocknsfw` | Configured Queen role | End an active interaction lock created by the same Queen |

### Verification helpers (moderation)

| Command | Access | Description |
| --- | --- | --- |
| `verified @user` | Manage Roles | Grant configured verified/NSFW-access role |
| `unverified @user` | Manage Roles | Remove verified role |

**Module:** `cogs.nsfw.*`, `cogs.interaction.nsfw_*`, `cogs.mod.verified`

---

## Booster perks

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `custom_role <color> <name>` | `booster_role` | Booster | Create booster custom role (`#RRGGBB` or gradient `#RRGGBB,#RRGGBB`; optional PNG icon attachment) |
| `update_custom_role <color> <name>` | `customroleupdate`, `boosterroleupdate` | Booster | Update existing booster role |
| `custom_room <name>` | `booster_room` | Booster | Create private voice room under configured category |

**Background (`janitor_unboosted`):** scheduled cleanup of custom roles/rooms after boost expires.

**Module:** `cogs.booster.*`

---

## Job reminders

| Command | Access | Description |
| --- | --- | --- |
| `jobremind` | Everyone | Subcommand guide |
| `jobremind add` | Everyone | Interactive timed DM reminder (duration then job name) |

**Background:** minute loop sends due DMs and removes completed task records.

**Module:** `cogs.job_remind.job_remind`

---

## Community utilities

### Giveaways

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `giveaway <duration> [winners] <prize>` | `ga` | Administrator / Manage Guild / Manage Messages | Start giveaway (e.g. `1h30m`, `2d`); persistent join/leave buttons |
| `giveaway list` | `ls`, `active` | Everyone (guild) | List active giveaways |
| `giveaway entries [message_id]` | `entrants`, `joined`, `who` | Everyone (guild) | Who joined a giveaway |
| `giveaway end [message_id]` | — | Host or Administrator / Manage Guild / Manage Messages | End early and pick winners; accepts an ID or replied giveaway message |
| `giveaway reroll [message_id] [winner_count]` | `rr` | Host or Administrator / Manage Guild / Manage Messages | Reroll 1–20 winners; accepts an ID or replied ended giveaway message |

Duration range: 10 seconds–30 days; max 20 winners.

### Votes

| Command | Access | Description |
| --- | --- | --- |
| `vote [yesno\|multiple\|multiplechoice] [question]` | Everyone | Interactive poll (asks for duration; multiple-choice collects options). Ends on schedule with results |

### Other utils

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `quote [image] [message_link\|message_id]` | `q`, `quotes` | Everyone (guild) | Quote a replied/current-channel message as a text embed by default. Add `image` before the optional link/ID to generate a PNG card with bundled offline emoji/symbol fallback fonts. Both modes use the author's server avatar when available, link to the original message, and have a 5s per-user cooldown |
| `big_speaker <size> <message>` | `loa`, `speaker` | Everyone (guild) | Re-speak a message in large Discord markdown. **`size` is 1–6**; TC cost by size: **1 / 2 / 5 / 10 / 20 / 50**. Sizes 5–6 add separators; 6 is bold H1. Mentions: user only; strips `@everyone`, `@here`, role pings. 30s cooldown |
| `random_member <@member\|@role>` | — | Everyone | Pick a random member (from role members if a role is given) |
| `save_image <collection> [key value ...]` | — | Manage Messages | Persist attached images + optional metadata pairs to Mongo (`images`) |

**Module:** `cogs.utils.*`

---

## Moderation

### Member actions

| Command | Access | Description |
| --- | --- | --- |
| `kick @user [reason]` | Kick Members | Kick member; records moderation case |
| `ban @user [reason]` | Ban Members | Ban member; records case |
| `softban @user [reason]` | Ban Members | Soft-ban / handcuff role workflow (stores previous roles) |
| `unsoftban @user [reason]` | Ban Members | Restore roles after softban |
| `mute @user [reason]` | Manage Roles | Assign Muted role |
| `unmute @user [reason]` | Manage Roles | Remove Muted role |
| `timeout @user <minutes> [reason]` | Moderate Members | Discord timeout |
| `untimeout @user [reason]` | Moderate Members | Clear timeout |
| `warn @user [reason]` | Manage Messages | Store warning + case |
| `check_warn [@user]` | Everyone (guild) | Recent warnings (default: self) |
| `nickchange @user <new_nick>` | Manage Nicknames | Change member nickname |
| `roleroll @user` | Manage Roles | Open a role dropdown, then assign the selected role |
| `roleunroll @user` | Manage Roles | Open a role dropdown, then remove the selected role |
| `rolecopy @source @target` | Manage Roles | Add eligible source roles missing from the target; preserve existing target roles and never mention/ping copied roles |

### Messages & channel controls

| Command | Access | Description |
| --- | --- | --- |
| `purge <n>` | Manage Messages | Delete last *n* messages in channel |
| `purge_user @user <n>` | Manage Messages | Delete last *n* messages from a user in channel |
| `clean_before <days>` | Manage Messages | Delete messages older than *n* days |
| `slowmode` | Manage Messages | Slowmode guide group |
| `slowmode check_bypass [@member]` | Everyone | Check channel permission overwrites |
| `slowmode immune @member` | Manage Messages | Exempt member from slowmode |
| `slowmode prominent @member` | Manage Messages | Remove the member's slowmode bypass overwrite |

### Cases

| Command | Access | Description |
| --- | --- | --- |
| `case` | Manage Messages | Usage guide |
| `case view <number>` | Manage Messages | View case by number |
| `case history @user [limit]` | Manage Messages | Last 1–10 cases for a member |
| `case edit <number> <reason>` | Manage Messages | Edit case reason |
| `case status <number> <open\|resolved\|appealed\|void>` | Manage Messages | Update case status |
| `case log_channel [#channel]` | Manage Guild | Set the mod-log channel (defaults to current channel) |

### Area 51 guard

| Command | Aliases | Access | Description |
| --- | --- | --- | --- |
| `area51_fire` | `area51_bump_now`, `area51_remind_now` | Administrator | Send Area 51 warning immediately |

**Background:** honeypot channel monitoring, cancel-ban UI, auto-prune, weekly reminder task. Configured via `AREA_51_CHANNEL_ID` / prune variables in Mongo settings.

**Module:** `cogs.mod.*`

---

## Discipline (automatic)

No user command. On message, if content matches entries in `data/banned_word_list.txt`:

1. Log breach to `discipline_logs`
2. React ⚠️ and warn the author (violation count)
3. Delete the warning after a few seconds and delete the original message

**Module:** `cogs.discipline.discipline`

---

## Settings (runtime configuration)

| Command | Access | Description |
| --- | --- | --- |
| `setting` | Administrator | Settings group help |
| `setting set_variable <NAME>` | Administrator | Interactive set of a Mongo `global_variables` key (loaded into `bot.global_vars`) |
| `setting get_variable <NAME>` | Administrator | Read a stored variable |

Common variable examples (not exhaustive): channel IDs, booster category, Area 51 channel, media arrays, `BETA_ROLE_IDS`.

**Module:** `cogs.settings.variable_setting`

---

## Seasonal / one-shot

| Feature | Type | Description |
| --- | --- | --- |
| Lunar New Year 2026 | Message listener | During a fixed UTC window (2026-02-16–17), messages containing keywords like `năm mới`, `nmvv`, `2026`, or `new year` get a one-time personal greeting |

**Module:** `cogs.happy_new_year.happy_lunar_new_year_2026`

---

## Internal helpers (not loadable as cogs)

These modules support features but are not discovered as extensions (leading `_`):

| Module | Role |
| --- | --- |
| `cogs._beta_function` | `@BetaFunction` decorator and access checks |
| `cogs._feature_flags` | `DISABLED_COGS` pattern parsing |
| Underscore helpers in packages | Shared validation, case recording, meters, shop helpers, setup diagnostics, role colors |

---

## Quick index by prefix (flat list)

```
help, mod, nsfw
hello, invite, verify, ping, beta_preview, server_stats, leave
setup, setup check
triggerreply, triggerreply add, triggerreply list, triggerreply remove
afk, afk dynamic, afk time, afk clear, afk check
random_femboy
daily, user_balance, user_transactions, add_tc, remove_tc, set_tc, check_tc
shop, shop buy, shop inventory, shop use, shop unequip, shop add_role, shop add_badge, shop remove
slot, flip_coin, sicbo_start
noitu, noitu status, noitu hint, noitu end, noitu analyze
vtv, vtv status, vtv next, vtv hint
gay, les, ship, penisize, aura, redflag, based, brainrot, clown, cope, cringe, delulu,
gyatt, ick, mainchar, npc, ohio, rizz, simp, skillissue, touchgrass, yapper,
femboycard, birthday, birthday set
kiss, hug, pat, slap, punch, hit, poke, cuddle, snuggle, boop, handhold, bonk, bite, stare, lick, smack,
avatar, server_avatar, propose, marriage, marriage help, marriage top, divorce, rank, cat, dog, 36
r34, gbr, nsfwrule, bj, rj, hj, fj, aj, tj, spank, frot, fuck, cream, 3some, orgy, ranknsfw, mrank
locknsfw, unlocknsfw, verified, unverified
custom_role, update_custom_role, custom_room
jobremind, jobremind add
giveaway, giveaway list, giveaway entries, giveaway end, giveaway reroll
vote, quote, big_speaker, random_member, save_image
kick, ban, softban, unsoftban, mute, unmute, timeout, untimeout, warn, check_warn
nickchange, roleroll, roleunroll, rolecopy
purge, purge_user, clean_before
slowmode, slowmode check_bypass, slowmode immune, slowmode prominent
case, case view, case history, case edit, case status, case log_channel
area51_fire
setting, setting set_variable, setting get_variable
```

**Automatic features:** random bot activity rotation at random 5–15 minute intervals; welcome / goodbye / ban announcements, AFK monitoring, banned-word discipline, booster unboost janitor, birthday announcements, job-reminder loop, giveaway/vote end scheduling, Area 51 honeypot, Lunar New Year greeting, word-game message handling.

Bot status data uses `type` + `think` for `CUSTOM` entries. All other activity types use `type` + `text` so their action-card text remains prominent.

---

## Keeping this file up to date

When you add or remove a command:

1. Update the relevant section and the flat index.
2. If structure or ownership of modules changes, also update `CODEBASE.md`.
3. Prefer documenting the public `name=` / aliases / permission checks from the cog, not private helper functions.
)
