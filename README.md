# TFVN Bot

TFVN Bot is a Vietnamese-first Discord community bot built for **Trap & Femboy VN**. It brings moderation, member onboarding, reminders, booster perks, persistent giveaways, social commands, an in-server economy, and Vietnamese word games into one modular `discord.py` application.

The bot uses prefix commands (the default prefix is `!tf `), stores persistent state in MongoDB, and organizes features as independently loaded cogs.

For a complete list of commands and automatic features, see [FUNCTIONS.md](FUNCTIONS.md).

> [!WARNING]
> This repository includes optional adult/NSFW cogs and third-party booru integrations. Only enable them for an adult community, keep them restricted to age-gated Discord channels, and follow Discord's rules and the upstream services' terms.

## Highlights

- **Community management:** welcome and differentiated leave/kick/ban announcements, verification, AFK tracking, birthdays, reminders, votes, and giveaways.
- **Moderation:** kick, ban, soft-ban, mute, timeout, warnings, numbered audit cases, message cleanup, slow mode, nickname/role tools, and the Area 51 guard workflow.
- **Booster perks:** custom roles and voice rooms, with automatic cleanup after a member stops boosting.
- **Games and economy:** the global, persistent Tiên Lộ AFK cultivation game, daily Trap Coins, a configurable role/badge shop, transaction history, slots, coin flips, Sic Bo, Vietnamese word chaining (`noitu`), and Vua Tiếng Việt (`vtv`).
- **Social and fun commands:** member interactions, rankings, avatars, random members, community-themed cards, and a collection of playful “meter” commands.
- **Optional age-restricted features:** NSFW interactions and Rule34/Gelbooru searches, guarded by Discord's NSFW channel setting.
- **Persistent state:** MongoDB-backed balances, cultivation profiles, interactions, game context, reminders, settings, giveaways, booster resources, and moderation data.

## How it works

`main.py` creates the Discord bot, enables the member and message-content intents, loads static datasets from `data/`, and connects the cogs to the MongoDB database created in `db.py`.

Cog loading depends on `ENVIRONMENT`:

- `production` scans `cogs/` recursively and attempts to load every Python module whose filename does not start with `_`.
- `development` loads only the dotted module paths listed in the ignored `dev_cogs.txt` file. Wildcards such as `cogs.mod.*` are supported.

`DISABLED_COGS` accepts comma-separated dotted paths or wildcard patterns and
skips matching extensions before import.

The settings cog is always prioritized when present. It loads the MongoDB `global_variables` collection into `bot.global_vars` before feature cogs initialize. If an individual cog is missing its required configuration, the loader reports that failure and continues loading the remaining cogs.

## Requirements

- Python 3.11 (the version used by the Docker image)
- A Discord application and bot token
- A reachable, credentialed MongoDB deployment
- Optional Rule34 and Gelbooru API credentials if those cogs are enabled

In the Discord Developer Portal, enable these privileged gateway intents for the bot:

- **Server Members Intent**
- **Message Content Intent**

The bot also needs the Discord permissions used by the cogs you enable. For example, moderation and booster features require permissions such as Manage Messages, Manage Roles, Manage Channels, Moderate Members, Kick Members, Ban Members, or View Audit Log. View Audit Log lets departure announcements distinguish moderator kicks from members leaving voluntarily.

## Local development

### 1. Clone and install

```powershell
git clone https://github.com/quynhcolleen/tfvn_bot.git
cd tfvn_bot
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On macOS or Linux, activate the environment with `source venv/bin/activate` instead.

### 2. Configure the environment

Create a `.env` file in the repository root:

```dotenv
DISCORD_TOKEN=replace_with_your_bot_token
ENVIRONMENT=development
COMMAND_PREFIX="!tf "

DB_METHOD=mongodb
DB_USERNAME=tfvn_bot
DB_PASSWORD=replace_with_a_strong_password
DB_HOST=localhost:27017
DB_NAME=tfvn_bot

# Optional feature controls
DISABLED_COGS=

# Used by cogs.general when that cog is enabled
INVITE_LINK=https://discord.com/oauth2/authorize?...
VERIFY_CHANNEL=123456789012345678
```

`db.py` constructs the connection as:

```text
DB_METHOD://DB_USERNAME:DB_PASSWORD@DB_HOST/?retryWrites=true&w=majority
```

Include the port in `DB_HOST`; the current connection builder does not read `DB_PORT`. Keep `.env` and `.env.prod` private—they are already ignored by Git.

Feature controls:

| Variable | Behavior |
| --- | --- |
| `DISABLED_COGS` | Comma-separated module patterns, such as `cogs.nsfw.*` |

### Role-gated Beta command decorator

Import `BetaFunction` and place it directly above a command callback:

```python
from discord.ext import commands

from cogs._beta_function import BetaFunction


@commands.command(name="new_preview")
@BetaFunction
async def new_preview(ctx: commands.Context) -> None:
    await ctx.send("This Beta function is enabled.")
```

The callback is loaded with the normal bot, but runs only when the member has at
least one role configured by `BETA_ROLE_IDS`. Set it as an `ARRAY` with
`!tf setting set_variable BETA_ROLE_IDS`, one role ID per line.
This setting is read only from MongoDB's `global_variables`; `.env` role values
are ignored. Denied checks receive a safe message instead of running the
callback. The shipped `!tf beta_preview` command can verify the configuration.

### 3. Select development cogs

Create `dev_cogs.txt` with a small startup profile:

```text
cogs.settings.variable_setting
cogs.general
cogs.help
cogs.operation.heartbeat
```

Add one dotted module path per line as you work. For example, `cogs.mod.*` loads every public module under `cogs/mod/`.
The profile supports blank lines, comments, explicit modules, and `.*`
wildcards.

For Tiên Lộ development, also load `cogs.cultivation.cultivation` together with
the account cogs used to view and earn Trap Coin.

### 4. Run the bot

```powershell
python main.py
```

When startup succeeds, the console prints `Bot is ready!`. Runtime output is also appended to `bot.log`.

## Server-specific settings

Most server IDs and feature assets used by the feature cogs are stored in MongoDB rather than `.env` (`INVITE_LINK` and `VERIFY_CHANNEL` are notable environment-based settings). With `cogs.settings.variable_setting` loaded, an administrator can set a value interactively:

```text
!tf setting set_variable JOIN_CHANNEL
```

The bot then asks whether the value is a `STRING` or `ARRAY` and prompts for its contents. Restart the bot after adding settings needed by cogs that previously failed to initialize.

Common settings include:

| Feature | MongoDB global variables | Type |
| --- | --- | --- |
| Join and leave/kick/ban announcements | `JOIN_CHANNEL`, `RULE_CHANNEL`, `ROLE_CHANNEL`, `BYE_CHANNEL` | `STRING` |
| Birthday announcements | `BIRTHDAY_CHANNEL` | `STRING` |
| Word games | `WORD_CONNECT_GAMES_CHANNELS`, `VIETNAMESE_KING_GAMES_CHANNELS` | `ARRAY` |
| Verification | `FALLEN_FEMBOY_ROLE_ID` | `STRING` |
| Booster placement | `BOOSTER_CUSTOM_ROLE_ANCHOR_ID`, `BOOSTER_CUSTOM_VOICE_CATEGORY_ID` | `STRING` |
| Area 51 guard | `AREA_51_CHANNEL_ID`, `AREA_51_PRUNE_HOURS` | `STRING` |
| Beta command access | `BETA_ROLE_IDS` | `ARRAY` |
| NSFW role controls | `KING_ROLE_ID`, `QUEEN_ROLE_ID` | `STRING` |
| NSFW interaction media | `BLOWJOB_GIFS`, `HANDJOB_GIFS`, `FOOTJOB_GIFS`, `ASSJOB_GIFS`, `THIGHJOB_GIFS`, `SPANK_GIFS`, `RIMJOB_GIFS`, `FROTTING_GIFS`, `FUCKING_GIFS`, `CREAMPIE_GIFS`, `THREESOME_GIFS`, `ORGY_GIFS` | `ARRAY` |

The booster role anchor is optional; without it, Discord keeps the custom role at its default position. `BOOSTER_CUSTOM_VOICE_CATEGORY_ID` is required for custom rooms so their private category placement and permission overwrites are deterministic. The word-chain move icons also have built-in emoji defaults.

Voluntary-leave, kick, and ban announcements all use `BYE_CHANNEL`. Give the bot
View Audit Log permission so it can reliably identify kicks; without that permission,
an unverified removal falls back to a generic departure announcement.

The shop and moderation cases keep guild-specific configuration in their own
MongoDB collections. Configure them with their admin
commands rather than `setting set_variable`:

| System | Initial configuration |
| --- | --- |
| Moderation cases | `!tf case log_channel #mod-log` |
| Shop | Add a role or badge item; no separate setup command is required |

### Optional booru API configuration

The `r34` and `gbr` cogs read their credentials from `.env`:

| Integration | Variables |
| --- | --- |
| Rule34 | `RULE34_API_URL`, `RULE34_API_KEY`, `RULE34_USER_ID`, `SECOND_RULE34_API_KEY`, `RULE34_SECOND_USER_ID` |
| Gelbooru | `GELBOORU_API_URL`, `GELBOORU_API_KEY`, `GELBOORU_USER_ID`, `SECOND_GELBOORU_API_KEY`, `GELBOORU_SECOND_USER_ID` |

The current implementation alternates between its primary and secondary credential pairs, so configure both pairs for reliable requests.

## Command overview

Commands are invoked with `COMMAND_PREFIX`. With the default prefix, `!tf help [topic]`
opens the bot's full command catalog split into overview, community, economy,
Tiên Lộ, games, fun/social, utilities/booster, automatic features, and moderation topics.
The catalog remains complete in partial development profiles; individual commands
still depend on loaded cogs, configuration, and Discord permissions. Only the NSFW
topic is hidden outside NSFW-marked channels. `!tf mod` and `!tf nsfw` open the same
menu focused on their respective topics.

| Area | Representative commands |
| --- | --- |
| General | `help [topic]`, `hello`, `invite`, `verify`, `ping`, `server_stats` |
| Community | `afk`, `jobremind add`, `birthday set`, `vote`, `giveaway` |
| Economy and games | `daily`, `user_balance`, `user_transactions`, `shop`, `slot`, `flip_coin`, `sicbo_start`, `noitu`, `vtv` |
| Tiên Lộ | `tutien`, `tutien thucong`, `tutien dotpha`, `tutien bicanh`, `tutien thiluyen`, `tutien doido` |
| Moderation | `kick`, `ban`, `softban`, `mute`, `timeout`, `warn`, `case`, `purge`, `slowmode`, `verified` |
| Operations | `ping`, `server_stats`, `setup check` |
| Booster tools | `custom_role`, `update_custom_role`, `custom_room` |
| Social and fun | `kiss`, `hug`, `pat`, `avatar`, `quote`, `rank`, `ship`, `aura`, `redflag`, configurable `triggerreply`, and other meter commands |
| Automatic features | Welcome and leave/kick/ban announcements, AFK monitoring, reminders, content filtering, scheduled cleanup, and persistent interaction handling |
| Optional NSFW | `nsfw`, `r34`, `gbr`, NSFW interactions, rankings, and role-based locks |

This table is only an overview. The in-Discord dropdown and [FUNCTIONS.md](FUNCTIONS.md)
provide the complete user-facing catalog; each module under `cogs/` remains the
implementation source of truth.

## Community systems

### Tiên Lộ cultivation game

Start a persistent profile and open its private dashboard with:

```text
!tf tutien batdau
!tf tutien
```

Tiên Lộ calculates Bế Quan rewards from timestamps, so AFK progress survives bot
restarts without a scheduler. Players choose Cân Bằng, Tĩnh Tu, or Khai Khoáng;
advance from Phàm Nhân through Kim Đan; select Kiếm Tu, Thể Tu, or Đan Tu; allocate
talents; and improve a four-slot equipment set through a deterministic market,
crafting, expeditions, and the 30-floor Tháp Thí Luyện.

Use `!tf tutien thucong` to collect AFK resources, `!tf tutien dotpha` to advance,
and `!tf tutien bicanh start <linhduoc|cokhoang|yeuthuson> <2|4|8>` for an
expedition. Only one Bế Quan/Bí Cảnh session can run at once. Major breakthroughs
use visible soft pity and never destroy Tu Vi or equipment on failure.

Trap Coin exchange is deliberately limited and uses a spread: members may spend
up to 50 TC per week at 1 TC = 10 Linh Thạch, or receive at most 20 TC per week at
20 Linh Thạch = 1 TC. Limits reset Monday at 00:00 Asia/Ho_Chi_Minh. PvP, player
trading, Tông Môn, theft, and Booster/paid power are not part of this release.

At startup, the cultivation cog ensures `user_accounts.user_id` is unique. If
legacy duplicate account documents prevent that index, the cog logs the duplicate
IDs and stays disabled; it does not guess how to merge Trap Coin balances.

See [CULTIVATE_GAME_PLAN.md](CULTIVATE_GAME_PLAN.md) for the complete design and
[FUNCTIONS.md](FUNCTIONS.md) for every command.

### Trap Coin shop

Administrators can add permanent role or badge ownership to the guild catalog:

```text
!tf shop add_role pink 100 @Pink A cosmetic pink role
!tf shop add_badge helper 250 Community Helper
```

Members use `shop`, `shop buy <item_id>`, `shop inventory`, and
`shop use <item_id>`. Purchases deduct balances atomically, reject duplicate
ownership, and write to `transaction_logs`. A badge remains owned when it is
unequipped.

### Moderation cases

Successful ban, kick, warn, timeout, mute, and soft-ban actions create a
guild-scoped numbered case. Moderators can use:

```text
!tf case view 12
!tf case history @member
!tf case edit 12 Updated reason
!tf case status 12 resolved
```

Reason and status edits retain an edit history. Status can be `open`,
`resolved`, `appealed`, or `void`.

Run `!tf setup check` after configuration to inspect MongoDB connectivity,
loaded cogs, channel/role IDs, bot permissions, and role hierarchy.

## Docker

The Compose service builds the bot, reads `.env`, and runs it with a restart policy:

```powershell
docker compose up --build -d
docker compose logs -f bot
```

Stop it with:

```powershell
docker compose down
```

The Compose file does **not** start MongoDB, so `DB_HOST` must point to an existing deployment reachable from the container. The bot opens no inbound port; it connects outbound to Discord, MongoDB, and any enabled external APIs.

The GitHub Actions workflow also builds and publishes container images to GitHub Container Registry for configured branches and releases.

## Tests

Run the unit-test suite from the repository root:

```powershell
python -m unittest discover -s test -p "test_*.py"
```

The automated tests cover cultivation calculations and state transitions, the
categorized help menu, meter formatting, quote-card rendering, cog flags, and
validation helpers used by the shop, cases, and setup diagnostics.

## Project structure

```text
tfvn_bot/
├── main.py                 # Bot startup, intents, data loading, and cog discovery
├── db.py                   # MongoDB client and database selection
├── dataloader.py           # JSON, text, line, and CSV data helpers
├── CULTIVATE_GAME_PLAN.md  # Tiên Lộ design and acceptance specification
├── cogs/                   # Discord commands, listeners, tasks, and UI views
│   ├── _beta_function.py   # Database-role guard for experimental commands
│   ├── _feature_flags.py   # Feature and cog disable flag parsing
│   ├── settings/           # Mongo-backed runtime variables
│   ├── economy/            # Trap Coin shop, inventory, badges, and role items
│   ├── cultivation/        # Tiên Lộ progression, AFK calculations, PvE, and economy
│   ├── mod/                # Moderation and verification
│   ├── booster/            # Booster custom roles/rooms and cleanup
│   ├── minigames/          # Economy games and Vietnamese word games
│   ├── interaction/        # Social and optional NSFW interactions
│   └── ...
├── data/                   # Word lists, filters, and game datasets
├── assets/                 # GIF and media constants
├── fonts/                  # Bundled quote-card fonts, licenses, and source notes
├── scripts/                # One-off data preparation/migration utilities
├── test/                   # Unit tests and development utilities
├── Dockerfile
└── docker-compose.yml
```

To add a cog, create a module containing an asynchronous `setup(bot)` function
and call `await bot.add_cog(...)`. Production discovers it automatically;
development uses its local cog profile. Prefix helper filenames with `_` when
they should not be loaded as extensions.
