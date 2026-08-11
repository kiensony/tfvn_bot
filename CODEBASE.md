# Codebase Map

This document maps the maintained repository files and explains where each behavior lives. It intentionally omits secrets and generated workstation artifacts such as `.env`, `.env.prod`, `.git/`, `.agents/`, `venv/`, `__pycache__/`, `.VSCodeCounter/`, and `bot.log`.

## Runtime Flow

1. `main.py` loads `.env`, creates the prefix-based `commands.Bot`, enables member and message-content intents, and attaches the MongoDB database from `db.py`.
2. `DataLoader` loads shared lists from `data/` onto the bot instance.
3. In production, every public Python module below `cogs/` is discovered recursively. Development uses the ignored `dev_cogs.txt`. Both use the database selected by `DB_NAME`.
4. `cogs.settings.variable_setting` is loaded first when selected, populating `bot.global_vars` from MongoDB.
5. Each extension registers commands, listeners, views, or scheduled tasks through `async def setup(bot)`.

## Repository Tree and Responsibilities

```text
tfvn_bot/
├── main.py                         Bot construction, events, data loading, cog discovery
├── db.py                           MongoDB client and selected database
├── dataloader.py                   UTF-8 JSON/text/line/CSV loading helpers
├── requirements.txt                Pinned Python runtime dependencies
├── Dockerfile                      Python 3.11 multi-stage image
├── docker-compose.yml              Bot service and environment wiring; no Mongo service
├── .dockerignore                   Excludes secrets, tests, logs, and local artifacts
├── .gitattributes                  Normalizes tracked text files to LF line endings
├── .gitignore                      Excludes local configuration, logs, and Python artifacts
├── README.md                       Project overview, setup, configuration, and operation
├── AGENTS.md                       Short contributor and agent entry guide
├── CODEBASE.md                     This ownership and architecture map
├── FUNCTIONS.md                    Full user-facing command and feature catalog
├── CULTIVATE_GAME_PLAN.md           Tiên Lộ gameplay, economy, and acceptance specification
├── CODING_CONVENSION.md            Detailed implementation conventions
├── sample.dev_cogs.txt             Legacy development-cog sample; review paths before use
│
├── .github/workflows/
│   ├── build_and_push.yml          Builds and publishes images to GHCR
│   └── notificate_to_discord.yml   Sends tag notifications to Discord
│
├── assets/
│   ├── gifs.py                     Welcome and general interaction media URLs
│   └── nsfw_gifs.py                Legacy NSFW media lists used by migration tooling
│
├── fonts/
│   ├── NotoSans-Variable.ttf        Primary Vietnamese-capable quote-card font
│   ├── NotoEmoji-Variable.ttf       Offline fallback for Unicode emoji glyphs
│   ├── NotoSansSymbols-Variable.ttf Common symbol and music-glyph fallback
│   ├── NotoSansSymbols2-Regular.ttf Decorative symbol and dingbat fallback
│   ├── *-OFL.txt / OFL.txt          SIL Open Font licenses for bundled fonts
│   └── README.md                    Font sources, hashes, and bundling notes
│
├── data/
│   ├── banned_word_list.txt        Discipline filter terms
│   ├── bot_activity_funny_status.json
│   │                                 Random Discord custom/action definitions
│   ├── fake_loading_sentences.txt  Random progress text for fun commands
│   ├── femboy_role.txt             Role names used by the femboy card command
│   ├── nsfw_channel.json           Verification-managed NSFW channel definitions
│   ├── vietnamese_king_data.json   Generated Vua Tiếng Việt puzzle dataset
│   └── word_connect_valid_list.txt Valid Vietnamese word-chain entries
│
├── scripts/
│   ├── migrate_nsfw_gifs.py        Moves legacy GIF lists into Mongo global variables
│   ├── vietnamese_king_data_prepare.py
│   │                                 Normalizes/filter source words and generates game data
│   └── words.txt                   Source records for Vietnamese data preparation
│
├── test/
│   ├── test_community_features.py  Pure validation/time/helper regression tests
│   ├── test_cultivation.py         Tiên Lộ calculations, state, UI, and persistence tests
│   ├── test_help_menu.py           Help catalog completeness, limits, gates, and UI tests
│   ├── test_meter_number_bars.py   unittest coverage for signed meter formatting
│   └── word_stardardlize.py        Manual normalization utility; not auto-discovered as a test
│
└── cogs/
    ├── __init__.py                 Root extension package marker
    ├── _beta_function.py           Multi-role Beta command access guard
    ├── _feature_flags.py           DISABLED_COGS pattern parsing
    ├── general.py                  hello, invite, and verification-channel pointers
    ├── help.py                     Full-catalog dropdown help UI with an NSFW channel gate
    ├── afk_remind/
    │   ├── afk_set.py              Timed/dynamic AFK setup, clearing, and ping review
    │   └── afk_monitor.py          AFK mention capture and return detection
    ├── announcement/
    │   ├── __init__.py             Announcement package marker
    │   ├── welcome.py              Member-join announcement
    │   ├── goodbye.py              Member-leave announcement
    │   └── banned.py               Member-ban announcement
    ├── booster/
    │   ├── _role_colors.py         Solid/gradient role-color parsing helper
    │   ├── create_custom_role.py   Booster-owned custom role creation
    │   ├── update_custom_role.py   Booster custom role edits
    │   ├── create_custom_room.py   Booster private voice-room creation
    │   └── janitor_unboosted.py    Scheduled cleanup after boosts expire
    ├── cotd/random_femboy.py       Random saved image and social metadata lookup
    ├── cultivation/
    │   ├── __init__.py             Cultivation package marker
    │   ├── cultivation.py          Tiên Lộ commands, dashboard, and atomic persistence
    │   └── _cultivation_helpers.py Pure realms, rewards, market, PvE, and exchange rules
    ├── daily_reward/
    │   ├── daily_action.py         Daily Trap Coin grant and claim tracking
    │   └── user_account.py         Balance, badge, and transaction-history lookup
    ├── economy/
    │   ├── _shop_helpers.py        Catalog ID, price, and display validation
    │   └── shop.py                 Guild catalog, purchases, inventory, roles, and badges
    ├── discipline/discipline.py    Banned-word listener, logging, warning, and deletion
    ├── funny_things/
    │   ├── _meter_helper.py        Deterministic scores, bars, loading, and embed helpers
    │   ├── aura.py                 Signed aura score and icon bar
    │   ├── redflag.py              Signed red/green flag score and icon bar
    │   ├── birthday.py             Birthday registration and announcement task
    │   ├── femboy_card.py          Member card based on configured role names
    │   ├── gay_meter.py            Daily member meter with staged loading
    │   ├── penisize.py             Daily member meter with staged loading
    │   ├── ship_meter.py           Two-member compatibility meter
    │   └── based.py, brainrot.py, clown.py, cope.py, cringe.py, delulu.py,
    │       gyatt.py, ick.py, les_meter.py, mainchar.py, npc.py, ohio.py,
    │       rizz.py, simp.py, skillissue.py, touchgrass.py, yapper.py
    │                                 Shared-helper-based daily meter commands
    ├── happy_new_year/
    │   └── happy_lunar_new_year_2026.py
    │                                     Time-limited one-time Lunar New Year greeting
    ├── interaction/
    │   ├── cat.py, dog.py             External animal-image API commands
    │   ├── meme_interaction.py        Static meme response command
    │   ├── user_interaction.py        Social actions, avatar display, and rankings
    │   ├── marriage.py                Propose/divorce/status, couple XP ranks
    │   ├── _marriage_helpers.py       Pure level/rank/XP helpers for marriage
    │   ├── triggered_reply.py          Persistent phrase-triggered replies
    │   ├── _trigger_reply_helpers.py   Rule parsing and matching helpers
    │   ├── nsfw_interaction.py        Age-gated interactions and rankings
    │   └── nsfw_super_user.py         Role-controlled NSFW lock/unlock workflow
    ├── job_remind/job_remind.py       Persistent timed DM reminders
    ├── minigames/
    │   ├── flip_coin/flip_coin.py     Coin betting against user balances
    │   ├── slot_machine/slot_machine.py
    │   │                                 Slot betting, payouts, and transaction logs
    │   ├── sicbo/sicbo.py             Reaction-based Sic Bo rounds
    │   ├── word_connect/word_connect.py
    │   │                                 Persistent Vietnamese word-chain game
    │   └── vietnamese_king/vietnamese_king.py
    │                                     Persistent letter-scramble game
    ├── mod/
    │   ├── _case_helpers.py         Safe shared case recording and validation
    │   ├── cases.py                 Numbered moderation audit trail and log config
    │   ├── ban.py, kick.py             Basic member removal actions
    │   ├── mute.py, timeout.py          Temporary restriction controls
    │   ├── softban.py                   Soft-ban and role restoration data
    │   ├── purge.py, janitor.py         Message cleanup commands
    │   ├── nickname.py, role.py         Nickname and role management
    │   ├── slowmode.py                  Slow-mode inspection and overrides
    │   ├── warn.py                      Warning commands
    │   ├── verified.py                  Verified role and NSFW channel access management
    │   └── area_51_guard.py             Honeypot channel, cancel view, bans, and reminders
    ├── nsfw/
    │   ├── __init__.py             NSFW extension package marker
    │   ├── r34.py                       Age-gated Rule34 API search
    │   └── gelbooru.py                  Age-gated Gelbooru API search
    ├── operation/
    │   ├── bot_status.py                Random Discord activity and timing rotation
    │   ├── _setup_helpers.py           Pure setup-check result and ID helpers
    │   ├── heartbeat.py                 Latency/health command
    │   ├── server_stats.py              In-memory uptime and command/error counts
    │   ├── setup_check.py               Database, permission, ID, and cog diagnostics
    │   └── leave.py                     Administrator-controlled guild departure
    ├── settings/variable_setting.py     Mongo-backed runtime variable commands
    └── utils/
        ├── giveaway.py                  Persistent views, entries, scheduling, and rerolls
        ├── vote.py                      Persistent reaction polls and result scheduling
        ├── quote.py                     Text-embed and PNG message quote modes
        ├── _quote_card.py               Quote text wrapping and PNG card rendering
        ├── big_speaker.py               Paid TC big-text re-speak in current channel
        ├── _big_speaker_helpers.py      Size 1–6 → TC cost, mention sanitize, format helpers
        ├── random_member.py             Random guild member selection
        └── save_image.py                Discord attachment metadata persistence
```

Local `dev_cogs.txt` selects extensions during development. `DISABLED_COGS` can
filter loaded extensions with exact dotted modules or wildcard patterns.
`draft.txt` is a local scratch file.

## Persistence Boundaries

MongoDB collections are created lazily. Major groups are:

- Configuration: `global_variables`, `moderation_config`
- Economy: `user_accounts` (including versioned `cultivation` state), `daily_rewards_logs`, `transaction_logs`, `shop_items`, `shop_inventory`
- Cultivation audit: append-only `cultivation_events`; TC exchanges also write `transaction_logs`
- Social state: `interactions`, `nsfw_settings`, `images`, `marriages`, `marriage_proposals`, `triggered_replies`
- Scheduling: `tasks`, `votes`, `giveaways`, `birthdays`, `birthday_announcements`
- AFK and moderation: `afk_reminders`, `afk_pings`, `discipline_logs`, `old_roles`, `warnings`, `moderation_cases`
- Shared sequence counters: `feature_counters`
- Games and boosters: `context`, `sicbo_active_games`, `booster_custom_roles`, `booster_custom_rooms`

Discord tokens, database credentials, and external API credentials belong in
environment variables. Runtime database selection uses `DB_NAME`.
Process-level extension controls use `DISABLED_COGS`; guild-specific IDs and
media arrays generally belong in `global_variables`.

Tiên Lộ stores its authoritative profile below `user_accounts.cultivation` and
keeps Trap Coin in `user_accounts.balance`, allowing an exchange to update both
balances atomically. Writes use a cultivation revision for compare-and-swap and
idempotent request IDs, with at most three retries. The cog requires a unique
`user_accounts.user_id` index; if duplicate account documents prevent the index,
it logs the problem and remains disabled rather than merging balances.

Commands decorated with `@BetaFunction` are registered normally in any runtime,
but the callback requires the invoking member to hold at least one role in
`BETA_ROLE_IDS`. The role list is read only from `bot.global_vars`, populated by
the MongoDB `global_variables` collection; environment role values are ignored.

## Where to Make a Change

- Add or change a command/listener in its domain under `cogs/`.
- Put reusable feature helpers in a leading-underscore module beside their consumers.
- Put shared static media in `assets/`; put runtime-editable media in Mongo settings.
- Treat large game datasets as generated outputs and update their preparation script with them.
- Add deterministic regression tests under `test/test_*.py`.
- Update this map whenever files move, a new subsystem appears, or ownership changes.
