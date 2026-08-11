# Tiên Lộ — Cultivation Game Plan

## Summary

Implement a complete initial release of the **Tiên Lộ** cultivation game. Players
progress through AFK cultivation, choose one of three classes, allocate talents,
collect and craft equipment, clear tower trials, run expeditions, use soft-pity
breakthroughs, and exchange a limited amount of Trap Coin each week.

PvP, player trading, Tông Môn, player theft, and paid or Booster cultivation
advantages are intentionally outside this release.

## Core Progression

### Realms and resources

- Realms progress through `Phàm Nhân`, `Luyện Khí 1–9`, and the
  `Sơ/Trung/Hậu/Viên Mãn` stages of both Trúc Cơ and Kim Đan.
- `Tu Vi` is earned through cultivation and spent on breakthroughs. It cannot be
  traded, and excess Tu Vi remains after a successful breakthrough.
- `Linh Thạch` is earned through cultivation and expeditions. It pays for cave
  upgrades, equipment, crafting, breakthrough fees, and talent resets.
- `Điểm Thiên Phú` cannot be purchased. Minor breakthroughs grant one point and
  major breakthroughs grant two points.

### AFK cultivation

- A player starts Bế Quan once; collecting rewards automatically resumes the
  selected cultivation focus.
- Rewards are calculated lazily from stored timestamps. No background reward loop
  is required, and progress survives bot downtime.
- Base offline storage is 24 hours. Động Phủ upgrades add four hours per level,
  capped at 48 hours.
- Claims shorter than ten minutes do not settle, preventing micro-claim spam.
- Cultivation focus modifiers are:

| Focus | Tu Vi | Linh Thạch |
| --- | ---: | ---: |
| Cân Bằng | 100% | 100% |
| Tĩnh Tu | 125% | 60% |
| Khai Khoáng | 75% | 150% |

- Động Phủ starts at level one and ends at level seven. Each purchased level adds
  5% production; upgrade costs are
  `500 / 1,500 / 4,000 / 10,000 / 25,000 / 60,000` Linh Thạch.
- A player may have only one active Bế Quan or Bí Cảnh session.

### Classes and talents

The player chooses one class at Luyện Khí 1:

- `Kiếm Tu`: PvE power, expedition speed, and breakthrough chance.
- `Thể Tu`: PvE power, offline capacity, and reduced breakthrough failure fees.
- `Đan Tu`: PvE power, crafting discounts, and increased expedition material
  yield.

Each class has three talents with five ranks each. Global bonus caps are +75%
offline production, −30% expedition time or crafting cost, and +20 percentage
points to breakthrough chance.

A full class/talent reset refunds all allocated points, costs
`1,000 × current realm index` Linh Thạch, and has a seven-day cooldown. It is
invoked with `tutien phai reset`.

### Breakthroughs

- Minor-stage breakthroughs always succeed.
- Major-realm breakthroughs start at a 70% success chance.
- Every failed major attempt adds ten percentage points; the fourth attempt is
  guaranteed.
- Talent bonuses apply before the final 100% cap.
- Failure keeps all Tu Vi, equipment, and realm progress. Its base fee is 25% of
  the listed Linh Thạch cost; the Thể Tu Hộ Mạch talent can reduce that fee to
  10%. A failed attempt starts a one-hour retry cooldown.
- Success consumes the full requirement, resets pity, grants talent points, and
  preserves excess Tu Vi.

## Equipment and PvE

### Equipment economy

Equipment uses four slots: `Pháp Khí`, `Pháp Bào`, `Pháp Bảo`, and
`Nhẫn Trữ Vật`. Items have fixed visible statistics; there are no randomized stat
rolls or durability.

All three acquisition paths are included:

- A permanent basic market and four deterministic offers rotating by ICT date,
  with no paid rerolls.
- Guaranteed crafting recipes using expedition materials.
- Boss drops with a visible pity counter. The tenth eligible clear guarantees
  gear; duplicate gear is automatically converted into crafting fragments.

### Tower and expeditions

- `Tháp Thí Luyện` contains 30 deterministic, one-time floors. Every fifth floor
  is a boss, and major breakthroughs require the corresponding tower floor.
- `Bí Cảnh` supports two-, four-, and eight-hour expeditions focused on herbs,
  Linh Thạch/materials, or equipment.
- Bế Quan and Bí Cảnh cannot run simultaneously. When an expedition finishes, the
  previous cultivation focus resumes automatically, even if rewards are collected
  later.

## Trap Coin Exchange

Exchange limits reset every Monday at 00:00 in `Asia/Ho_Chi_Minh`:

- Spend at most 50 TC per week at `1 TC = 10 Linh Thạch`.
- Earn at most 20 TC per week at `20 Linh Thạch = 1 TC`.
- The spread prevents exchange arbitrage.
- Talent points cannot be purchased.

## Public Interface

The main prefix-command group is `tutien`, with alias `cultivate`:

- `tutien batdau`, `tutien thucong`,
  `tutien huong <canbang|tinhtu|khaikhoang>`, `tutien dotpha`
- `tutien phai [kiem|the|dan]`, `tutien phai reset`
- `tutien thienphu`, `tutien thienphu tang <talent_id> [points]`
- `tutien dongphu`, `tutien dongphu nangcap`
- `tutien choden`, `tutien mua <item_id>`, `tutien kho`,
  `tutien trangbi <item_id>`, `tutien phanra <item_id>`,
  `tutien luyen [recipe_id]`
- `tutien thiluyen [tang]`
- `tutien bicanh`,
  `tutien bicanh start <linhduoc|cokhoang|yeuthuson> <2|4|8>`,
  `tutien bicanh claim`, `tutien bicanh cancel`
- `tutien doido`, `tutien doido mua <amount_tc>`,
  `tutien doido ban <so_linh_thach>`
- `tutien profile [@member]`, `tutien top`,
  `tutien riengtu [public|private]`

The bare `tutien` command opens an owner-only dashboard with buttons and selects
for common actions. Prefix subcommands remain complete fallbacks. Replies mention
only the invoking member, while denied component interactions are ephemeral.

Profiles are global. `tutien top` compares only visible members in the current
guild, and private profiles are excluded.

## Persistence and Reliability

- Store a versioned `cultivation` object inside `user_accounts`; Trap Coin remains
  in the same account document so exchanges can update both balances atomically.
- Use integer arithmetic and state-normalization helpers.
- Use compare-and-swap writes with no more than three retries and request IDs for
  idempotency.
- Append gameplay audit records to `cultivation_events` and Trap Coin exchange
  records to `transaction_logs`.
- Require a unique index on `user_accounts.user_id`. If duplicate user documents
  exist, report them and leave the cultivation cog disabled rather than merging
  balances automatically.
- Keep realm, item, recipe, shop, tower, expedition, and talent definitions in
  data tables so future content does not require a persistence-schema rewrite.

## Documentation and Development Integration

- Implement the feature under `cogs/cultivation/`, with public cog code in
  `cultivation.py` and pure calculations in `_cultivation_helpers.py`.
- Add a dedicated Tiên Lộ topic to the dropdown help menu.
- Document the complete command surface in `FUNCTIONS.md`, the subsystem and
  persistence ownership in `CODEBASE.md`, setup/usage in `README.md`, and the cog
  in `sample.dev_cogs.txt`.

## Test Plan

- Verify offline reward calculation, fractional remainders, focus modifiers,
  24–48-hour storage caps, future timestamps, and restart recovery.
- Prove concurrent or repeated claims, exchanges, purchases, expeditions, and
  breakthroughs cannot double-credit or overspend resources.
- Test realm transitions, excess Tu Vi, injected breakthrough rolls, pity,
  fourth-attempt guarantees, failure fees, and retry cooldowns.
- Test class selection, talent limits and resets, equipment ownership/equipping,
  crafting, duplicate salvage, deterministic market rotation, and boss-drop pity.
- Test deterministic tower requirements and two-, four-, and eight-hour
  expedition settlement.
- Test exchange rates, weekly ICT reset boundaries, caps, atomicity, transaction
  records, and no-arbitrage behavior.
- Test dashboard ownership, timeout behavior, profile privacy, mention controls,
  Discord embed limits, and complete help/documentation coverage.
- Run the focused cultivation tests, then the full unittest suite and
  `git diff --check`.

## Acceptance Criteria

- Every gameplay system selected for the initial release is available.
- Offline progress requires no scheduler and survives bot downtime.
- Repeated and concurrent requests cannot duplicate resources or overspend
  balances.
- Failed breakthroughs never destroy Tu Vi, equipment, or realm progress.
- No random shop rerolls, loot boxes, PvP, trading, Tông Môn, player theft, or
  message-based farming are present.
- This plan accurately matches the implemented behavior, and all automated tests
  pass.
