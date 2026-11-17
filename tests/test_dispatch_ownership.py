"""One terminal, one owning session: the newest team row that is neither dead nor empty owns it,
and a session that may not take it is refused with the terminal, the owner and the way out."""

import os
import subprocess
import sys

from dispatch_harness import DEAD_GENERATION, stop
from test_mcp_server import RUNTIME_SRC
from tools_harness import ToolCase

sys.path.insert(0, str(RUNTIME_SRC))
import pytest
from agent_runtime import agent_runtime as lib
from agent_runtime.dispatch_store import Store, StoreError, TerminalRefused

pytestmark = pytest.mark.integration


class TeamOwnershipTests(ToolCase):
    """One rule owns a terminal: the newest team row that is neither dead nor empty.

    A row is dead when its generation is a `pid:lstart` stamp whose process no longer carries that
    stamp, and empty when it holds no worker, assignment, message, receipt, caller, task, last_wait
    row or operation.
    """

    def setUp(self):
        super().setUp()
        self.store = Store({"HOME": str(self.dir / "home")})
        self.scope, self.terminal = "scope", "t0"

    def live_generation(self):
        """A generation whose process runs right now: this test process."""
        return "%d:%s" % (os.getpid(), lib.lstart(os.getpid()))

    def other_live_generation(self):
        """A generation of another process that runs right now."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        # Cleanups run last-in-first-out: a `wait` registered after `kill` would run first and sit out the
        # whole sleep. One cleanup kills, then reaps, so a live process costs no test time.
        self.addCleanup(stop, proc)
        return "%d:%s" % (proc.pid, lib.lstart(proc.pid))

    def stray_team(self, generation):
        """A newer team row for the terminal, written with SQL.

        The store's registration refuses a session that does not own the terminal, so a fixture that
        needs the state that refusal is about writes the row itself."""
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO teams VALUES (?,?,?,?)",
                ("stray", self.scope, self.terminal, generation),
            )
        return "stray"

    @pytest.mark.spec("Terminal ownership")
    def test_S3_a_dead_and_empty_newer_row_does_not_fence_the_live_lead(self):
        """A session that left nothing behind never fences the terminal."""
        live = self.live_generation()
        team = self.store.team(self.scope, self.terminal, live)
        self.store.register(team, "w1")  # the live lead's team holds work
        self.stray_team(DEAD_GENERATION)  # newer, and empty
        actor = self.store.actor(self.scope, self.terminal, None, generation=live)
        self.assertEqual(actor["team_id"], team)

    @pytest.mark.spec("Terminal ownership")
    def test_S4_a_live_newer_row_owns_the_terminal_and_fences_the_live_lead(self):
        """The live lead is fenced with 'stale team generation' and the message names the owner."""
        live = self.live_generation()
        team = self.store.team(self.scope, self.terminal, live)
        self.store.register(team, "w1")
        owner = self.other_live_generation()
        self.stray_team(owner)
        with self.assertRaises(StoreError) as error:
            self.store.actor(self.scope, self.terminal, None, generation=live)
        message = str(error.exception)
        self.assertIn("stale team generation", message)
        self.assertIn(self.terminal, message)
        self.assertIn(owner, message)

    @pytest.mark.spec("Terminal ownership")
    def test_S5_a_dead_newer_row_that_holds_work_keeps_the_terminal(self):
        """A generation that is gone but still holds work keeps the terminal."""
        live = self.live_generation()
        team = self.store.team(self.scope, self.terminal, live)
        self.store.register(team, "w1")
        gone = self.stray_team(DEAD_GENERATION)
        self.store.register(gone, "w2")  # the gone session still holds a worker
        with self.assertRaises(StoreError) as error:
            self.store.actor(self.scope, self.terminal, None, generation=live)
        message = str(error.exception)
        self.assertIn("stale team generation", message)
        self.assertIn(DEAD_GENERATION, message)


class TerminalRefusalTests(ToolCase):
    """One terminal, one owning session: the refusal names the terminal, the owner and the way out.

    The store's team registration is the one seam both registration sites share, so a session that
    does not own the terminal registers nothing and is refused. The process evidence is injected,
    so a test can stand for the process table.
    """

    def setUp(self):
        super().setUp()
        self.env = {"HOME": str(self.dir / "home")}
        self.store = Store(self.env)
        self.scope, self.terminal = "scope", "t0"

    def live_generation(self):
        """A generation whose process runs right now: this test process."""
        return "%d:%s" % (os.getpid(), lib.lstart(os.getpid()))

    def other_live_generation(self):
        """A generation of another process that runs right now."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        # Cleanups run last-in-first-out: a `wait` registered after `kill` would run first and sit out the
        # whole sleep. One cleanup kills, then reaps, so a live process costs no test time.
        self.addCleanup(stop, proc)
        return "%d:%s" % (proc.pid, lib.lstart(proc.pid))

    def dead_generation(self):
        """A generation whose process is gone: no process carries that start stamp."""
        return "%d:%s" % (os.getpid(), "a stamp no process carries")

    def hold_work(self, team):
        """The owner's team holds live work: a worker whose assignment is running.

        The state is written with SQL, the way the store holds it: the rule reads a team that is
        serving, and a fixture writes that state down."""
        with self.store.transaction() as db:
            db.execute("INSERT INTO workers VALUES (?,?,?,?,?)", ("w9", team, "w1", "t9", 1))
            db.execute("INSERT INTO assignments VALUES (?,?,?,'working',1)", ("a9", team, "w9"))

    def stray_team(self, generation):
        """A team row for another generation, written with SQL.

        The store refuses a generation it does not own, so a fixture that needs the state that
        refusal is about writes the row itself."""
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO teams VALUES (?,?,?,?)",
                ("stray", self.scope, self.terminal, generation),
            )

    def teams(self):
        with self.store.transaction() as db:
            return [
                dict(row) for row in db.execute("SELECT id,generation FROM teams ORDER BY rowid")
            ]

    @pytest.mark.spec("Terminal ownership")
    def test_S1_a_session_that_descends_from_the_owner_is_refused(self):
        """the session that descends from the owner never takes the terminal."""
        owner = self.live_generation()
        team = self.store.team(self.scope, self.terminal, owner)
        self.store.register(team, "w1")  # the owner's team holds work
        owner_pid = int(owner.split(":")[0])
        descendant = Store(self.env, ancestors=lambda: [owner_pid])  # the owner is in this chain
        with self.assertRaises(TerminalRefused) as error:
            descendant.team(self.scope, self.terminal, "4242:descendant")
        message = str(error.exception)
        self.assertIn(self.terminal, message, "the refusal does not name the terminal")
        self.assertIn(owner, message, "the refusal does not name the owning generation")
        actor = self.store.actor(self.scope, self.terminal, None, generation=owner)
        self.assertEqual(actor["team_id"], team, "the owner lost its terminal")
        self.assertEqual(len(self.teams()), 1, "the descendant registered a team")

    @pytest.mark.spec("Terminal ownership")
    def test_S1_a_descendant_is_refused_even_when_the_owners_team_is_idle(self):
        """the owner's own session tree never takes the terminal, idle or not."""
        owner = self.live_generation()
        self.store.team(self.scope, self.terminal, owner)  # the owner's team holds nothing
        owner_pid = int(owner.split(":")[0])
        descendant = Store(self.env, ancestors=lambda: [owner_pid])
        with self.assertRaises(TerminalRefused):
            descendant.team(self.scope, self.terminal, "4242:descendant")
        self.assertEqual(len(self.teams()), 1, "the descendant registered a team")

    @pytest.mark.spec("Terminal ownership")
    def test_S1_an_unrelated_session_is_refused_while_the_owner_is_alive_and_holds_work(self):
        """a session that inherits a live lead's terminal is refused."""
        owner = self.live_generation()
        team = self.store.team(self.scope, self.terminal, owner)
        self.hold_work(team)  # the owner's team holds work
        with self.assertRaises(TerminalRefused):
            self.store.team(self.scope, self.terminal, "4242:unrelated")
        self.assertEqual(len(self.teams()), 1, "the refused session registered a team")

    def test_S1_an_unrelated_session_takes_a_terminal_whose_owner_is_idle(self):
        """stale-team-generation/D7: an idle terminal is free for the next session."""
        owner = self.live_generation()
        self.store.team(self.scope, self.terminal, owner)  # the owner's team holds nothing
        team = self.store.team(self.scope, self.terminal, "4242:unrelated")
        self.assertNotEqual(team, None)
        self.assertEqual(len(self.teams()), 2, "the next session did not register a team")

    @pytest.mark.spec("Terminal ownership")
    def test_S1_an_unrelated_session_takes_a_terminal_whose_owner_is_gone(self):
        """a session that left nothing behind never keeps the terminal."""
        self.stray_team(self.dead_generation())  # a newer row whose process is gone
        team = self.store.team(self.scope, self.terminal, "4242:unrelated")
        self.assertIsNotNone(team)
        self.assertEqual(len(self.teams()), 2, "the next session did not register a team")

    def test_the_process_evidence_is_injected(self):
        """The store reads the process table only through the readers it is given."""
        stamp = "a stamp the fixture wrote"
        alive = Store(self.env, alive=lambda _pid: (True, stamp))
        team = alive.team(self.scope, self.terminal, "4242:%s" % stamp)
        self.hold_work(team)
        with self.assertRaises(TerminalRefused):
            alive.team(self.scope, self.terminal, "4243:unrelated")
        self.assertEqual(len(self.teams()), 1, "the refused session registered a team")

    @pytest.mark.spec("Terminal ownership")
    def test_S6_a_session_that_registers_in_the_evidence_window_is_refused_not_a_crash(self):
        """a row that lands between the two reads is a refusal, not a crash.

        The evidence for the terminal's teams is gathered outside the transaction, so another session can
        register a row in that window: the owner the second read meets is a generation the evidence does
        not know yet, and the caller is refused with the message that names the terminal and the owner."""
        owner = self.live_generation()
        team = self.store.team(self.scope, self.terminal, owner)
        self.hold_work(team)
        rogue = "4242:rogue"
        landed = []

        def stamp(pid):
            """The process table: the rogue row lands while the evidence is gathered."""
            if pid == 4242:
                return (True, "rogue")
            if not landed:
                landed.append(True)
                with self.store.transaction() as db:
                    db.execute(
                        "INSERT INTO teams VALUES (?,?,?,?)",
                        ("rogue", self.scope, self.terminal, rogue),
                    )
            return (True, owner.split(":")[1])

        store = Store(self.env, alive=stamp, ancestors=lambda: [4242])
        with self.assertRaises(TerminalRefused) as error:
            store.team(self.scope, self.terminal, "4343:second")
        message = str(error.exception)
        self.assertIn(self.terminal, message, "the refusal does not name the terminal")
        self.assertIn(rogue, message, "the refusal does not name the generation that owns it")
        self.assertEqual(len(self.teams()), 2, "the refused session registered a team")

    @pytest.mark.spec("Terminal ownership")
    def test_S6_the_refusal_and_the_fence_name_the_owner_and_the_way_out(self):
        """a refusal names the reason and the way out."""
        owner = self.live_generation()
        team = self.store.team(self.scope, self.terminal, owner)
        self.hold_work(team)  # the owner's team holds work
        with self.assertRaises(TerminalRefused) as error:
            self.store.team(self.scope, self.terminal, "12345:other-session")
        message = str(error.exception)
        self.assertIn(self.terminal, message, "the refusal does not name the terminal")
        self.assertIn(owner, message, "the refusal does not name the owning generation")
        self.assertIn("hc terminal", message, "the refusal does not name the way out")
        self.assertIn("new session", message, "the refusal does not name the way out")
        self.assertEqual(len(self.teams()), 1, "the refused session registered a team")
        newer = self.other_live_generation()
        self.stray_team(newer)
        with self.assertRaises(StoreError) as fenced:
            self.store.actor(self.scope, self.terminal, None, generation=owner)
        text = str(fenced.exception)
        self.assertIn("stale team generation", text)
        self.assertIn(self.terminal, text, "the fence does not name the terminal")
        self.assertIn(newer, text, "the fence does not name the owning generation")
