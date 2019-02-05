from threading import Timer
import asyncio
import logging
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler
import discord
from redbot.core import Config, commands, checks
from redbot.core.utils.chat_formatting import pagify

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(name)s: %(message)s')
LOG = logging.getLogger("autoreload")


class EventHandler(PatternMatchingEventHandler):
    """An asyncio-compatible debounced event handler for Watchdog."""

    def __init__(self, bot, cog_name, wait, logto=None, patterns=None):
        print(patterns)
        super().__init__(patterns=patterns, ignore_directories=True)
        self.bot = bot
        self.cog_name = cog_name
        self.wait = wait
        self.logto = logto

    async def reload(self):
        """ Reload the cog.
               Getting the exception when a cog fails to load is hacky at best.
               It is possible to retrieve the wrong exception. It also only
               returns the last exception and only if it is not a repeat
               exception. If you have a better idea please open an issue.
        """

        tmp_last_e = self.bot._last_exception
        core = self.bot.get_cog("Core")

        result = await core._reload([self.cog_name])
        if result[0]:
            if self.logto:
                await self.logto.send(f"Reloaded `{self.cog_name}`")
            LOG.info(f"*** Reloaded {self.cog_name} ***")

        else:
            if self.logto:
                await self.logto.send(f"`{self.cog_name}` failed to reload")

                if tmp_last_e != self.bot._last_exception:
                    for page in pagify(self.bot._last_exception, shorten_by=16):
                        await self.logto.send(f"```{page}```")

            LOG.info(f"**** {self.cog_name} failed to reload ****")

    def on_modified(self, event):
        """ Debounce Watchdog file modified events """
        LOG.debug(f"File modified: {event}")
        try:
            self._debounce.cancel()
        except AttributeError:
            pass

        def reload_debounced():
            self.bot.loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self.reload()
            )

        self._debounce = Timer(self.wait, reload_debounced)
        self._debounce.start()


class CogWatchdog:
    """ Watch a cogs path for any changes. Should be cross-platform. """

    def __init__(self, bot, cog_name, path, wait, logto=None, patterns=None):
        self.observer = Observer()
        self.observer.schedule(
            EventHandler(bot, cog_name, wait, logto, patterns),
            path,
            recursive=True
        )
        LOG.debug("Watchdog setup")

    def start(self):
        self.observer.start()

    def stop(self):
        self.observer.stop()
        self.observer.join()


class AutoReload(commands.Cog):
    """ Automatically reload cogs on file modification """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=18070201914174111403, force_registration=True
        )

        self.config.register_global(**{
            "logto": None,
            "wait": 5,  # seconds
            "patterns": ["*.py"],
            "cogs": [],
        })

        self.watch = {}
        LOG.info("Cog loaded!")

    async def load(self):
        """ Load cogs on start """

        async with self.config.cogs() as curr_cogs:
            LOG.info(f"Loading ALL: {curr_cogs}")
            for cog in curr_cogs:
                await self._add(cog)

    def unload(self):
        """ Unload cogs on stop """

        LOG.debug(f"Unloading ALL: {self.watch}")
        for cog in self.watch:
            self.watch[cog].stop()

        self.watch.clear()

    def __unload(self):
        LOG.info("Unloading cog!")
        self.unload()

    async def reload(self):
        """ Reload all cogs """

        LOG.debug("Reload ALL started")
        self.unload()
        await self.load()
        LOG.debug("Reload ALL finished")

    async def _add(self, cog_name):
        """ Add a single cog to be auto-reloaded. """

        cog = await self.bot.cog_mgr.find_cog(cog_name)

        LOG.debug(f"add => find_cog: {cog}")
        if cog and cog.name not in self.watch:
            async with self.config.cogs() as curr_cogs:
                if cog.name not in curr_cogs:
                    curr_cogs.append(cog.name)

            path = cog.submodule_search_locations[0]
            wait = await self.config.wait()
            logto = await self.config.logto()
            logto = self.bot.get_user(logto)
            patterns = await self.config.patterns()
            if not patterns:
                patterns.append("*")
            LOG.debug(f"Creating watch: (cog_name={cog.name},path={path},wait={wait},logto={logto},patterns={patterns})")

            self.watch[cog.name] = CogWatchdog(
                self.bot,
                cog.name,
                path,
                wait,
                logto,
                patterns
            )
            self.watch[cog.name].start()

            return True

    async def _del(self, cog_name):
        """ Delete a single cog from being auto-reloaded. """

        if cog_name in self.watch:
            LOG.debug(f"Removing cog: {cog_name}")
            async with self.config.cogs() as curr_cogs:
                curr_cogs.remove(cog_name)

            self.watch[cog_name].stop()
            del self.watch[cog_name]
            return True

    @commands.group(name="autoreload", aliases=["ar"])
    @checks.is_owner()
    async def autoreload(self, ctx: commands.Context):
        """ Tired of running [p]reload <cog name> when developing a cog?
            Let [p]autoreload do that for you! """
        pass

    @autoreload.command(name="log")
    async def log(self, ctx: commands.Context, enabled: bool = None, user: discord.User = None):
        """ true|false [@user] - Notify on reloads and send exceptions to `@owner` or `@user`. """

        if enabled is None:
            userid = await self.config.logto()
            user = self.bot.get_user(userid)
            if user:
                await ctx.send(f"Logging is currently enabled and sending to `{user.mention}`")
            else:
                await ctx.send("Logging is currently disabled")

        elif enabled:
            if not user:
                owner = await self.bot.application_info()
                user = owner.owner
            await self.config.logto.set(user.id)
            await ctx.send("Logging enabled")
            LOG.info(f"Logging enabled => {user.name}")
            await self.reload()

        elif not enabled:
            await self.config.logto.set(None)
            await ctx.send("Logging disabled")
            LOG.info(f"Logging disabled")
            await self.reload()

    @autoreload.command(name="wait")
    async def wait(self, ctx: commands.Context, wait: int = None):
        """ wait (in seconds) - Wait `n` seconds before reloading. Used to prevent rapid reloading. `0` to disable. """

        if wait is None:
            wait = await self.config.wait()
        elif wait >= 0:
            await self.config.wait.set(wait)
            LOG.info(f"Wait set: {wait}")
            await self.reload()

        await ctx.send(f"Wait set to `{wait} seconds`")

    @autoreload.group(name="patterns", aliases=["pat"])
    async def patterns(self, ctx: commands.Context):
        """ add|rm pattern[s] - Add/remove pattern(s) to monitor for changes. No patterns == Wildcard match """
        pass

    @patterns.command(name="add")
    async def patterns_add(self, ctx: commands.Context, *patterns: str):
        """ pattern[s] - Add pattern(s) to monitor for changes. """

        added = []
        async with self.config.patterns() as curr_patterns:
            for pattern in patterns:
                if pattern not in curr_patterns:
                    curr_patterns.append(pattern)
                    added.append(pattern)

        if added:
            await ctx.send(f"Added pattern(s): `{'`, `'.join(added)}`")
            LOG.info(f"Added patterns: {added}")
            await self.reload()
        else:
            await ctx.send("No patterns added!")

    @patterns.command(name="rm", aliases=["remove", "del", "delete"])
    async def patterns_remove(self, ctx: commands.Context, *patterns: str):
        """ pattern[s] - Remove pattern(s). """

        removed = []
        async with self.config.patterns() as curr_patterns:
            for pattern in patterns:
                if pattern in curr_patterns:
                    curr_patterns.remove(pattern)
                    removed.append(pattern)

        if removed:
            await ctx.send(f"Removed pattern(s): `{'`, `'.join(removed)}`")
            LOG.info(f"Removed patterns: {removed}")
            await self.reload()
        else:
            await ctx.send("No patterns removed!")

    @patterns.command(name="ls", aliases=["list"])
    async def patterns_list(self, ctx: commands.Context):
        """ - List pattern(s) being monitored for changes. """

        async with self.config.patterns() as curr_patterns:
            await ctx.send(f"Current patterns: `{'`, `'.join(curr_patterns) if curr_patterns else 'none' }`")

    @autoreload.command(name="add", aliases=["start"])
    async def add(self, ctx: commands.Context, *cogs: str):
        """ cog_name[s] - Add one or more cogs to be auto-reloaded. """

        added = []
        for cog in cogs:
            if await self._add(cog):
                added.append(cog)

        if added:
            await ctx.send(f"Auto-reloading started for `{'`, `'.join(added)}`")
            LOG.info(f"Added: {added}")

        failed = list(set(cogs) - set(added))
        if failed:
            await ctx.send(f"Auto-reloading failed for `{'`, `'.join(failed)}`. Double check cog name.")
            LOG.info(f"Add failed: {failed}")

    @autoreload.command(name="rm", aliases=["remove", "del", "delete", "stop"])
    async def remove(self, ctx: commands.Context, *cogs: str):
        """ cog_name[s] - Remove one or more cogs from being auto-reloaded. """

        removed = []
        for cog in cogs:
            if await self._del(cog):
                removed.append(cog)

        if removed:
            await ctx.send(f"Auto-reload stopped for `{'`, `'.join(removed)}`")
            LOG.info(f"Removed: {removed}")

        failed = list(set(cogs) - set(removed))
        if failed:
            await ctx.send(f"Auto-reloading failed to stop for `{'`, `'.join(failed)}`")
            LOG.info(f"Remove failed: {failed}")

    @autoreload.command(name="ls", aliases=["list"])
    async def list(self, ctx: commands.Context):
        """ - List cogs currently being auto-reloaded. """

        async with self.config.cogs() as curr_cogs:
            await ctx.send(f"Auto-reloaded cogs: `{'`, `'.join(curr_cogs) if curr_cogs else 'none' }`")

    @autoreload.command(name="debug", hidden=True)
    async def debug(self, ctx: commands.Context, enabled: bool = None):
        """ true|false - Enable debugging (console) """

        if enabled is None:
            if LOG.level is logging.DEBUG:
                enabled = False
            else:
                enabled = True

        if enabled:
            LOG.setLevel(logging.DEBUG)
        else:
            LOG.setLevel(logging.INFO)

        LOG.info(f"AR DEBUG: {'ENABLED' if enabled else 'DISABLED'}")
        await ctx.send(f"AR DEBUG: `{'ENABLED' if enabled else 'DISABLED'}`")
