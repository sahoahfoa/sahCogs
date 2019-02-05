from .autoreload import AutoReload

async def setup(bot):
    ar = AutoReload(bot)
    await ar.load()
    bot.add_cog(ar)
    