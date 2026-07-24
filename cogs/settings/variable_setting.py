import discord
from discord.ext import commands, tasks
import asyncio

class VariableSetting(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.variable = None  # Initialize the variable to None
        self.db = bot.db  # Assuming bot has a db attribute for database operations

        self.VARIABLE_TYPE = {
            'STRING': "STRING",
            "ARRAY": "ARRAY",
        }

        self.collection = self.db['global_variables']  
        self.bot.global_vars = self.load_variables()  # Load variables into bot's global_vars dict

    def save_variable(self, name: str, type: str, value: str | list) -> None:
        """Saves or updates a variable in the database."""
        self.collection.update_one(
            {'name': name},
            {'$set': {'name': name, 'type': type, 'value': value}},
            upsert=True
        )
        # Update the in-memory dict
        self.bot.global_vars[name] = value

    def load_variables(self) -> dict:
        """Loads all global variables from the database into a dict."""
        variables = {}
        for doc in self.collection.find():
            variables[doc['name']] = doc['value']
        return variables

    @commands.group(name='setting', invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def setting(self, ctx):
        """Group command for settings."""
        await ctx.send("Use subcommands to manage settings.")

    @setting.command(name='set_variable')
    @commands.has_permissions(administrator=True)
    async def set_variable(self, ctx, name: str):
        """Sets a variable with the given name and value."""
        '''Prompt the user for the value of the variable.'''
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel
        await ctx.send(f"Please provide the value for the variable '{name}':")

        # msg = await self.bot.wait_for('message', check=check)
        # value = msg.content

        # Loop to validate variable type
        while True:
            try:
                embed = discord.Embed(
                    title="Nhập loại biến 🛠️",
                    description="Vui lòng nhập loại biến:\n`STRING` hoặc `ARRAY`\n\nType `cancel` để hủy.",
                    color=discord.Color.blurple(),
                )
                await ctx.send(embed=embed)
                msg_var_type = await self.bot.wait_for("message", check=check, timeout=120)
                var_type = msg_var_type.content.upper().strip()

                if var_type == "CANCEL":
                    await ctx.send("✅ Đã hủy thiết lập biến.")
                    return

                if var_type not in self.VARIABLE_TYPE:
                    await ctx.send("❌ Loại biến không hợp lệ. Vui lòng nhập `STRING` hoặc `ARRAY`.")
                    continue  # Loop again

                # Valid type, break the loop
                break

            except asyncio.TimeoutError:
                await ctx.send("⏰ Hết thời gian chờ. Vui lòng thử lại.")
                return

        # Now prompt for value based on type
        while True:
            try:
                if var_type == "STRING":
                    await ctx.send("Vui lòng nhập giá trị cho biến (bất kỳ chuỗi nào, không rỗng):")
                elif var_type == "ARRAY":
                    await ctx.send("Vui lòng nhập giá trị cho biến (mỗi mục trên một dòng, ví dụ:\n```\nitem1\nitem2\nitem3\n```):")

                msg_value = await self.bot.wait_for("message", check=check, timeout=120)
                value_input = msg_value.content.strip()

                if value_input.lower() == "cancel":
                    await ctx.send("✅ Đã hủy thiết lập biến.")
                    return

                # Validate value based on type
                if var_type == "STRING":
                    if not value_input:
                        await ctx.send("❌ Giá trị không được rỗng. Vui lòng thử lại hoặc type `cancel`.")
                        continue
                    value = value_input  # Store as string
                elif var_type == "ARRAY":
                    if not value_input:
                        await ctx.send("❌ Giá trị không được rỗng. Vui lòng thử lại hoặc type `cancel`.")
                        continue
                    # Parse as newline-separated list
                    value_list = [item.strip() for item in value_input.split("\n") if item.strip()]
                    if not value_list:
                        await ctx.send("❌ Danh sách không hợp lệ (tất cả mục đều rỗng). Vui lòng thử lại hoặc type `cancel`.")
                        continue
                    # Additional check: Ensure no empty items (already handled by strip and filter, but explicit)
                    if any(not item for item in value_list):
                        await ctx.send("❌ Danh sách có mục rỗng. Vui lòng thử lại hoặc type `cancel`.")
                        continue
                    value = value_list  # Store as list

                # Valid value, break the loop
                break

            except asyncio.TimeoutError:
                await ctx.send("⏰ Hết thời gian chờ. Vui lòng thử lại.")
                return

        setattr(self, name, value)
        self.save_variable(name, var_type, value)
        await ctx.send(f"✅ Biến '{name}' đã được thiết lập thành ```{value}``` (loại: {var_type}).")

    @setting.command(name='get_variable')
    @commands.has_permissions(administrator=True)
    async def get_variable(self, ctx, requested_name: str):
        """Gets the value of a variable by name."""
        name = ctx.invoked_with
        for var_name, var_value in self.bot.global_vars.items():
            if var_name == requested_name:
                await ctx.send(f"Variable '{var_name}' has value: '{var_value}'")
                return
            
        await ctx.send(f"Variable '{requested_name}' not found.")
    

async def setup(bot):
    await bot.add_cog(VariableSetting(bot))
