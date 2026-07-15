# TFVN Bot

TFVN Bot is a Vietnamese-first Discord community bot built for **Trap & Femboy VN**. It brings moderation, member onboarding, reminders, booster perks, persistent giveaways, social commands, an in-server economy, and Vietnamese word games into one modular `discord.py` application.

The bot uses prefix commands (the default prefix is `!tf `), stores persistent state in MongoDB, and organizes features as independently loaded cogs.

> [!WARNING]
> This repository includes optional adult/NSFW cogs and third-party booru integrations. Only enable them for an adult community, keep them restricted to age-gated Discord channels, and follow Discord's rules and the upstream services' terms.

## Highlights

- **Community management:** welcome, goodbye, ban announcements, verification, AFK tracking, birthdays, reminders, votes, and giveaways.
- **Moderation:** kick, ban, soft-ban, mute, timeout, warnings, message cleanup, slow mode, nickname/role tools, and the Area 51 guard workflow.
- **Booster perks:** custom roles and voice rooms, with automatic cleanup after a member stops boosting.
- **Games and economy:** daily Trap Coins, slots, coin flips, Sic Bo, Vietnamese word chaining (`noitu`), and Vua Tiếng Việt (`vtv`).
- **Social and fun commands:** member interactions, rankings, avatars, random members, community-themed cards, and a collection of playful “meter” commands.
- **Optional age-restricted features:** NSFW interactions and Rule34/Gelbooru searches, guarded by Discord's NSFW channel setting.
- **Persistent state:** MongoDB-backed balances, interactions, game context, reminders, settings, giveaways, booster resources, and moderation data.

## How it works

`main.py` creates the Discord bot, enables the member and message-content intents, loads static datasets from `data/`, and connects the cogs to the MongoDB database created in `db.py`.

Cog loading depends on `ENVIRONMENT`:

- `production` scans `cogs/` recursively and attempts to load every Python module whose filename does not start with `_`.
- `development` loads only the dotted module paths listed in the ignored `dev_cogs.txt` file. Wildcards such as `cogs.mod.*` are supported.

The settings cog is always prioritized when present. It loads the MongoDB `global_variables` collection into `bot.global_vars` before feature cogs initialize. If an individual cog is missing its required configuration, the loader reports that failure and continues loading the remaining cogs.

## Requirements

- Python 3.11 (the version used by the Docker image)
- A Discord application and bot token
- A reachable, credentialed MongoDB deployment
- Optional Rule34 and Gelbooru API credentials if those cogs are enabled

In the Discord Developer Portal, enable these privileged gateway intents for the bot:

- **Server Members Intent**
- **Message Content Intent**

The bot also needs the Discord permissions used by the cogs you enable. For example, moderation and booster features require permissions such as Manage Messages, Manage Roles, Manage Channels, Moderate Members, Kick Members, or Ban Members.

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

# Used by cogs.general when that cog is enabled
INVITE_LINK=https://discord.com/oauth2/authorize?...
VERIFY_CHANNEL=123456789012345678
```

`db.py` constructs the connection as:

```text
DB_METHOD://DB_USERNAME:DB_PASSWORD@DB_HOST/?retryWrites=true&w=majority
```

Include the port in `DB_HOST`; the current connection builder does not read `DB_PORT`. Keep `.env` and `.env.prod` private—they are already ignored by Git.

### 3. Select development cogs

Create `dev_cogs.txt` with a small startup profile:

```text
cogs.settings.variable_setting
cogs.general
cogs.help
cogs.operation.heartbeat
```

Add one dotted module path per line as you work. For example, `cogs.mod.*` loads every public module under `cogs/mod/`.

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
| Join/leave announcements | `JOIN_CHANNEL`, `RULE_CHANNEL`, `ROLE_CHANNEL`, `BYE_CHANNEL` | `STRING` |
| Birthday announcements | `BIRTHDAY_CHANNEL` | `STRING` |
| Word games | `WORD_CONNECT_GAMES_CHANNELS`, `VIETNAMESE_KING_GAMES_CHANNELS` | `ARRAY` |
| Verification | `FALLEN_FEMBOY_ROLE_ID` | `STRING` |
| Booster placement | `BOOSTER_CUSTOM_ROLE_ANCHOR_ID`, `BOOSTER_CUSTOM_VOICE_CATEGORY_ID` | `STRING` |
| Area 51 guard | `AREA_51_CHANNEL_ID`, `AREA_51_PRUNE_HOURS` | `STRING` |
| NSFW role controls | `KING_ROLE_ID`, `QUEEN_ROLE_ID` | `STRING` |
| NSFW interaction media | `BLOWJOB_GIFS`, `HANDJOB_GIFS`, `RIMJOB_GIFS`, `FROTTING_GIFS`, `FUCKING_GIFS`, `CREAMPIE_GIFS`, `THREESOME_GIFS`, `ORGY_GIFS` | `ARRAY` |

The booster placement values are optional; without them, Discord creates the resource without placing it under a configured anchor/category. The word-chain move icons also have built-in emoji defaults.

### Optional booru API configuration

The `r34` and `gbr` cogs read their credentials from `.env`:

| Integration | Variables |
| --- | --- |
| Rule34 | `RULE34_API_URL`, `RULE34_API_KEY`, `RULE34_USER_ID`, `SECOND_RULE34_API_KEY`, `RULE34_SECOND_USER_ID` |
| Gelbooru | `GELBOORU_API_URL`, `GELBOORU_API_KEY`, `GELBOORU_USER_ID`, `SECOND_GELBOORU_API_KEY`, `GELBOORU_SECOND_USER_ID` |

The current implementation alternates between its primary and secondary credential pairs, so configure both pairs for reliable requests.

## Command overview

Commands are invoked with `COMMAND_PREFIX`. With the default prefix, `!tf help`, `!tf mod`, and `!tf nsfw` display the built-in user, moderator, and age-restricted help menus.

| Area | Representative commands |
| --- | --- |
| General | `help`, `hello`, `invite`, `verify`, `ping`, `server_stats` |
| Community | `afk`, `jobremind add`, `birthday set`, `vote`, `giveaway`, `random_member` |
| Economy and games | `daily`, `user_balance`, `slot`, `flip_coin`, `sicbo_start`, `noitu`, `vtv` |
| Moderation | `kick`, `ban`, `softban`, `mute`, `timeout`, `warn`, `purge`, `slowmode`, `verified` |
| Booster tools | `custom_role`, `update_custom_role`, `custom_room` |
| Social and fun | `kiss`, `hug`, `pat`, `avatar`, `rank`, `ship`, `aura`, `redflag`, and other meter commands |
| Optional NSFW | `nsfw`, `r34`, `gbr`, NSFW interactions, rankings, and role-based locks |

This is an overview rather than an exhaustive command reference; the source of truth is each module under `cogs/`.

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

The current automated tests cover shared formatting and icon-bar behavior used by the meter commands.

## Project structure

```text
tfvn_bot/
├── main.py                 # Bot startup, intents, data loading, and cog discovery
├── db.py                   # MongoDB client and database selection
├── dataloader.py           # JSON, text, line, and CSV data helpers
├── cogs/                   # Discord commands, listeners, tasks, and UI views
│   ├── settings/           # Mongo-backed runtime variables
│   ├── mod/                # Moderation and verification
│   ├── booster/            # Booster custom roles/rooms and cleanup
│   ├── minigames/          # Economy games and Vietnamese word games
│   ├── interaction/        # Social and optional NSFW interactions
│   └── ...
├── data/                   # Word lists, filters, and game datasets
├── assets/                 # GIF and media constants
├── scripts/                # One-off data preparation/migration utilities
├── test/                   # Unit tests and development utilities
├── Dockerfile
└── docker-compose.yml
```

To add a cog, create a module containing an asynchronous `setup(bot)` function and call `await bot.add_cog(...)`. Production discovers it automatically; development requires adding its dotted path to `dev_cogs.txt`. Prefix helper filenames with `_` when they should not be loaded as extensions.
