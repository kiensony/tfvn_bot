import logging

import discord
from discord.ext import commands
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from cogs.economy._shop_helpers import (
    clean_display_text,
    format_price,
    normalize_item_id,
    validate_price,
)
from cogs.roles._role_safety import dangerous_permission_names


logger = logging.getLogger(__name__)

SHOP_ITEMS_COLLECTION = "shop_items"
SHOP_INVENTORY_COLLECTION = "shop_inventory"
ACCOUNTS_COLLECTION = "user_accounts"
TRANSACTIONS_COLLECTION = "transaction_logs"
MAX_CATALOG_ITEMS = 25


class ShopCog(commands.Cog):
    """Guild-specific Trap Coin catalog and member inventory."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.items = self.db[SHOP_ITEMS_COLLECTION]
        self.inventory = self.db[SHOP_INVENTORY_COLLECTION]
        self.accounts = self.db[ACCOUNTS_COLLECTION]
        self.transactions = self.db[TRANSACTIONS_COLLECTION]
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        try:
            self.items.create_index(
                [("guild_id", ASCENDING), ("item_id", ASCENDING)],
                unique=True,
                name="guild_item_unique",
            )
            self.items.create_index(
                [("guild_id", ASCENDING), ("enabled", ASCENDING)],
                name="guild_enabled_items",
            )
            self.inventory.create_index(
                [
                    ("guild_id", ASCENDING),
                    ("user_id", ASCENDING),
                    ("item_id", ASCENDING),
                ],
                unique=True,
                name="guild_user_item_unique",
            )
            self.transactions.create_index(
                [("user_id", ASCENDING), ("timestamp", DESCENDING)],
                name="user_transactions_recent",
            )
        except PyMongoError:
            logger.exception("Failed to create shop indexes")

    def _find_item(self, guild_id: int, item_id: str) -> dict | None:
        return self.items.find_one(
            {"guild_id": guild_id, "item_id": item_id, "enabled": True}
        )

    @staticmethod
    def _member_can_manage_role(member: discord.Member, role: discord.Role) -> bool:
        return member == member.guild.owner or member.top_role > role

    @commands.group(
        name="shop",
        aliases=["store"],
        invoke_without_command=True,
        help="Xem cửa hàng Trap Coin.",
    )
    @commands.guild_only()
    async def shop(self, ctx: commands.Context) -> None:
        catalog = list(
            self.items.find({"guild_id": ctx.guild.id, "enabled": True})
            .sort([("price", ASCENDING), ("item_id", ASCENDING)])
            .limit(MAX_CATALOG_ITEMS)
        )
        if not catalog:
            await ctx.send("Cửa hàng chưa có vật phẩm nào.")
            return

        embed = discord.Embed(
            title="🛍️ Cửa hàng Trap Coin",
            description=(
                f"Mua: {self.bot.command_prefix}shop buy <item_id> · "
                f"Sử dụng: {self.bot.command_prefix}shop use <item_id>"
            ),
            color=discord.Color.gold(),
        )
        for item in catalog:
            icon = "🎭" if item["item_type"] == "role" else "🏷️"
            embed.add_field(
                name=f"{icon} {item['name']} — {format_price(item['price'])}",
                value=(
                    f"ID: {item['item_id']} · "
                    f"{item.get('description', 'Không có mô tả')}"
                )[:1024],
                inline=False,
            )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @shop.command(name="buy", help="Mua một vật phẩm trong shop.")
    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.user)
    async def shop_buy(self, ctx: commands.Context, item_id: str) -> None:
        try:
            normalized_id = normalize_item_id(item_id)
        except ValueError:
            await ctx.send("Item ID không hợp lệ.")
            return

        item = self._find_item(ctx.guild.id, normalized_id)
        if item is None:
            await ctx.send("Không tìm thấy vật phẩm đang bán với ID đó.")
            return
        if item["item_type"] == "role":
            role = ctx.guild.get_role(int(item.get("role_id", 0)))
            bot_member = ctx.guild.me
            if (
                role is None
                or role.is_default()
                or role.managed
                or dangerous_permission_names(role.permissions)
                or bot_member is None
                or role >= bot_member.top_role
            ):
                await ctx.send(
                    "Role của vật phẩm này hiện không an toàn hoặc bot không thể gán. "
                    "Bạn chưa bị trừ Trap Coin."
                )
                return
            if role in ctx.author.roles:
                await ctx.send("Bạn đã có role của vật phẩm này.")
                return

        ownership_filter = {
            "guild_id": ctx.guild.id,
            "user_id": ctx.author.id,
            "item_id": normalized_id,
        }
        if self.inventory.find_one(ownership_filter):
            await ctx.send("Bạn đã sở hữu vật phẩm này.")
            return

        price = int(item["price"])
        self.accounts.update_one(
            {"user_id": ctx.author.id},
            {"$setOnInsert": {"balance": 0}},
            upsert=True,
        )
        account = self.accounts.find_one_and_update(
            {"user_id": ctx.author.id, "balance": {"$gte": price}},
            {"$inc": {"balance": -price}},
            return_document=ReturnDocument.AFTER,
        )
        if account is None:
            await ctx.send(
                f"Bạn không có đủ Trap Coin. Vật phẩm này giá {format_price(price)}."
            )
            return

        now = discord.utils.utcnow()
        try:
            self.inventory.insert_one(
                {
                    **ownership_filter,
                    "item_type": item["item_type"],
                    "name": item["name"],
                    "purchased_at": now,
                }
            )
        except DuplicateKeyError:
            self.accounts.update_one(
                {"user_id": ctx.author.id}, {"$inc": {"balance": price}}
            )
            await ctx.send("Bạn đã sở hữu vật phẩm này.")
            return
        except PyMongoError:
            self.accounts.update_one(
                {"user_id": ctx.author.id}, {"$inc": {"balance": price}}
            )
            logger.exception("Failed to save shop purchase; refunded user")
            await ctx.send("Không thể hoàn tất giao dịch. Trap Coin đã được hoàn lại.")
            return

        try:
            self.transactions.insert_one(
                {
                    "guild_id": ctx.guild.id,
                    "user_id": ctx.author.id,
                    "type": "shop_purchase",
                    "transaction_type": "debit",
                    "amount": price,
                    "item_id": normalized_id,
                    "timestamp": now,
                }
            )
        except PyMongoError:
            logger.exception("Failed to write shop transaction log")

        await ctx.send(
            f"Đã mua **{item['name']}** với {format_price(price)}. "
            f"Số dư còn lại: **{account.get('balance', 0):,} TC**.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @shop.command(name="inventory", aliases=["inv"], help="Xem kho vật phẩm.")
    @commands.guild_only()
    async def shop_inventory(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        target = member or ctx.author
        owned = list(
            self.inventory.find(
                {"guild_id": ctx.guild.id, "user_id": target.id}
            ).sort("purchased_at", ASCENDING)
        )
        if not owned:
            await ctx.send(f"{target.mention} chưa sở hữu vật phẩm nào trong shop.")
            return

        account = self.accounts.find_one({"user_id": target.id}) or {}
        active_badge = account.get("active_badge") or {}
        lines = []
        for record in owned[:MAX_CATALOG_ITEMS]:
            marker = (
                " · đang dùng"
                if active_badge.get("guild_id") == ctx.guild.id
                and active_badge.get("item_id") == record["item_id"]
                else ""
            )
            lines.append(f"• {record['item_id']} — {record['name']}{marker}")

        embed = discord.Embed(
            title=f"🎒 Kho đồ của {target.display_name}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @shop.command(name="use", help="Dùng role hoặc trang bị badge đã mua.")
    @commands.guild_only()
    async def shop_use(self, ctx: commands.Context, item_id: str) -> None:
        try:
            normalized_id = normalize_item_id(item_id)
        except ValueError:
            await ctx.send("Item ID không hợp lệ.")
            return

        owned = self.inventory.find_one(
            {
                "guild_id": ctx.guild.id,
                "user_id": ctx.author.id,
                "item_id": normalized_id,
            }
        )
        if owned is None:
            await ctx.send("Bạn chưa sở hữu vật phẩm này.")
            return

        item = self.items.find_one(
            {"guild_id": ctx.guild.id, "item_id": normalized_id}
        )
        if item is None:
            await ctx.send("Vật phẩm này không còn tồn tại trong catalog.")
            return

        if item["item_type"] == "badge":
            self.accounts.update_one(
                {"user_id": ctx.author.id},
                {
                    "$set": {
                        "active_badge": {
                            "guild_id": ctx.guild.id,
                            "item_id": item["item_id"],
                            "name": item["name"],
                        }
                    },
                    "$setOnInsert": {"balance": 0},
                },
                upsert=True,
            )
            await ctx.send(
                f"Đã trang bị badge **{item['name']}**.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        role = ctx.guild.get_role(int(item.get("role_id", 0)))
        bot_member = ctx.guild.me
        if role is None:
            await ctx.send("Role của vật phẩm này không còn tồn tại.")
            return
        if dangerous_permission_names(role.permissions):
            await ctx.send("Role này có quyền quản trị và không thể dùng qua shop.")
            return
        if role.managed or role >= bot_member.top_role:
            await ctx.send("Bot không thể gán role này do thứ bậc hoặc role được quản lý.")
            return
        if role in ctx.author.roles:
            await ctx.send(f"Bạn đang có role {role.mention} rồi.")
            return

        try:
            await ctx.author.add_roles(role, reason="Use purchased shop role")
        except discord.Forbidden:
            await ctx.send("Bot không có quyền gán role này.")
            return
        except discord.HTTPException:
            await ctx.send("Discord từ chối cập nhật role. Vui lòng thử lại.")
            return
        await ctx.send(
            f"Đã kích hoạt role {role.mention}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @shop.command(name="unequip", help="Gỡ badge đang trang bị.")
    @commands.guild_only()
    async def shop_unequip(self, ctx: commands.Context) -> None:
        self.accounts.update_one(
            {"user_id": ctx.author.id}, {"$unset": {"active_badge": ""}}
        )
        await ctx.send("Đã gỡ badge đang trang bị.")

    @shop.command(name="add_role", help="Thêm role vào shop.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def shop_add_role(
        self,
        ctx: commands.Context,
        item_id: str,
        price: int,
        role: discord.Role,
        *,
        description: str = "",
    ) -> None:
        try:
            normalized_id = normalize_item_id(item_id)
            valid_price = validate_price(price)
        except ValueError as exc:
            await ctx.send(str(exc))
            return

        if role.is_default() or role.managed:
            await ctx.send("Không thể bán role mặc định hoặc role được integration quản lý.")
            return
        dangerous = dangerous_permission_names(role.permissions)
        if dangerous:
            await ctx.send(
                "Không thể bán role có quyền quản trị: " + ", ".join(dangerous)
            )
            return
        if not self._member_can_manage_role(ctx.author, role):
            await ctx.send("Bạn chỉ có thể thêm role thấp hơn role cao nhất của mình.")
            return
        if role >= ctx.guild.me.top_role:
            await ctx.send("Role này phải thấp hơn role cao nhất của bot.")
            return

        await self._save_admin_item(
            ctx,
            item_id=normalized_id,
            name=role.name,
            description=description or f"Role {role.name}",
            price=valid_price,
            item_type="role",
            role_id=role.id,
        )

    @shop.command(name="add_badge", help="Thêm badge vào shop.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def shop_add_badge(
        self,
        ctx: commands.Context,
        item_id: str,
        price: int,
        *,
        display_name: str,
    ) -> None:
        try:
            normalized_id = normalize_item_id(item_id)
            valid_price = validate_price(price)
        except ValueError as exc:
            await ctx.send(str(exc))
            return

        await self._save_admin_item(
            ctx,
            item_id=normalized_id,
            name=display_name,
            description=f"Badge {display_name}",
            price=valid_price,
            item_type="badge",
        )

    async def _save_admin_item(
        self,
        ctx: commands.Context,
        *,
        item_id: str,
        name: str,
        description: str,
        price: int,
        item_type: str,
        role_id: int | None = None,
    ) -> None:
        now = discord.utils.utcnow()
        document = {
            "guild_id": ctx.guild.id,
            "item_id": item_id,
            "name": clean_display_text(name, fallback=item_id, limit=100),
            "description": clean_display_text(
                description, fallback="Không có mô tả", limit=300
            ),
            "price": price,
            "item_type": item_type,
            "enabled": True,
            "updated_at": now,
            "updated_by": ctx.author.id,
        }
        if role_id is not None:
            document["role_id"] = role_id

        self.items.update_one(
            {"guild_id": ctx.guild.id, "item_id": item_id},
            {"$set": document, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        await ctx.send(
            f"Đã lưu **{document['name']}** ({item_id}) với giá {format_price(price)}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @shop.command(name="remove", aliases=["disable"], help="Ẩn vật phẩm khỏi shop.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def shop_remove(self, ctx: commands.Context, item_id: str) -> None:
        try:
            normalized_id = normalize_item_id(item_id)
        except ValueError:
            await ctx.send("Item ID không hợp lệ.")
            return
        result = self.items.update_one(
            {"guild_id": ctx.guild.id, "item_id": normalized_id},
            {"$set": {"enabled": False, "updated_at": discord.utils.utcnow()}},
        )
        if result.matched_count == 0:
            await ctx.send("Không tìm thấy vật phẩm đó.")
            return
        await ctx.send(f"Đã ẩn {normalized_id} khỏi shop.")

    @shop.error
    async def shop_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Vui lòng thử lại sau {error.retry_after:.1f} giây.")
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn cần quyền Manage Server để quản lý shop.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("Tham số không hợp lệ. Dùng lệnh help để xem cú pháp.")
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShopCog(bot))
