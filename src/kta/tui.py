"""Live full-screen terminal cockpit for the daemon control plane."""

import curses
import json
import time
import uuid
from typing import Any, Dict, Optional

from .http import request_json


class ControlClient:
    def __init__(self, base_url: str, token: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": "Bearer {}".format(token)} if token else {}

    def get(self, path: str) -> Dict[str, Any]:
        return request_json("GET", self.base_url + path, headers=self.headers)

    def post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return request_json("POST", self.base_url + path, headers=self.headers, body=body)


def _chat(client: ControlClient, conversation_id: str, message: str) -> None:
    accepted = client.post("/v1/chat", {"conversation_id": conversation_id, "message": message})
    message_id = accepted["message_id"]
    print("queued chat job {}".format(accepted["job_id"]))
    while True:
        thread = client.get("/v1/conversations/{}?limit=50".format(conversation_id))
        seen_user = False
        for item in thread.get("messages") or []:
            if item["message_id"] == message_id:
                seen_user = True
                continue
            if seen_user and item["role"] == "assistant":
                print("\nagent> {}\n".format(item["content"]))
                return
        time.sleep(1)


def _clip(value: Any, width: int) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


class Dashboard:
    tabs = ("Overview", "Agents", "Universe", "Learning", "Events", "Trades")

    def __init__(self, screen, client: ControlClient):
        self.screen, self.client = screen, client
        self.tab, self.error, self.notice = 0, "", ""
        self.selection = 0
        self.data: Dict[str, Any] = {}
        self.last_refresh = 0.0

    def run(self) -> None:
        curses.curs_set(0)
        self.screen.nodelay(True)
        self.screen.timeout(250)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            for pair, color in enumerate((curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED), 1):
                curses.init_pair(pair, color, -1)
        while True:
            if time.monotonic() - self.last_refresh >= 2:
                self.refresh()
            self.draw()
            key = self.screen.getch()
            if key in {ord("q"), 27}:
                return
            if key in {curses.KEY_RIGHT, ord("l"), 9}:
                self.tab = (self.tab + 1) % len(self.tabs)
            elif key in {curses.KEY_LEFT, ord("h")}:
                self.tab = (self.tab - 1) % len(self.tabs)
            elif key == ord("r"):
                self.refresh()
            elif key == ord("d"):
                self.action("/v1/discovery", {"cause": "tui"}, "Discovery queued")
            elif key == ord("s"):
                self.action("/v1/scans", {"cause": "tui"}, "Universe scan queued")
            elif key == curses.KEY_DOWN:
                self.selection += 1
            elif key == curses.KEY_UP:
                self.selection = max(0, self.selection - 1)
            elif key in {ord("p"), ord("x")} and self.tab == 1:
                tasks = self.data.get("agents", [])
                if tasks:
                    task = tasks[min(self.selection, len(tasks) - 1)]
                    action = "cancel" if key == ord("x") else ("resume" if task["status"] == "paused" else "pause")
                    self.action("/v1/agents/{}".format(task["task_id"]), {"action": action}, "Task {}".format(action))

    def action(self, path: str, body: Dict[str, Any], notice: str) -> None:
        try:
            result = self.client.post(path, body)
            self.notice = "{} · job {}".format(notice, result.get("job_id", ""))
            self.error = ""
            self.refresh()
        except Exception as error:
            self.error = "{}: {}".format(type(error).__name__, error)

    def refresh(self) -> None:
        try:
            self.data = {
                "status": self.client.get("/v1/status"),
                "agents": self.client.get("/v1/agents?limit=100").get("tasks", []),
                "universe": self.client.get("/v1/universe?limit=20"),
                "experiments": self.client.get("/v1/experiments?limit=100").get("experiments", []),
                "events": self.client.get("/v1/events?limit=100").get("events", []),
                "suggestions": self.client.get("/v1/suggestions?limit=100").get("suggestions", []),
            }
            self.error = ""
        except Exception as error:
            self.error = "{}: {}".format(type(error).__name__, error)
        self.last_refresh = time.monotonic()

    def write(self, y: int, x: int, text: Any, style: int = 0) -> None:
        height, width = self.screen.getmaxyx()
        if 0 <= y < height and x < width:
            try:
                self.screen.addstr(y, x, _clip(text, max(0, width - x - 1)), style)
            except curses.error:
                pass

    def draw(self) -> None:
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        self.write(0, 2, " KTA  AUTONOMOUS INVESTMENT RESEARCH ", curses.A_BOLD | curses.color_pair(1))
        status = self.data.get("status", {})
        self.write(0, max(2, width - 34), "{} · uptime {}s".format(status.get("status", "offline"), status.get("uptime_seconds", 0)), curses.color_pair(2))
        x = 2
        for index, label in enumerate(self.tabs):
            self.write(2, x, " {} ".format(label), curses.A_REVERSE | curses.A_BOLD if index == self.tab else curses.A_DIM)
            x += len(label) + 3
        self.write(3, 0, "─" * max(0, width - 1), curses.color_pair(1))
        (self.draw_overview, self.draw_agents, self.draw_universe, self.draw_learning, self.draw_events, self.draw_trades)[self.tab]()
        footer = self.error or self.notice or "←/→ tabs   d discover   s scan   ↑/↓ select   p pause/resume   x cancel   q quit"
        self.write(height - 1, 1, footer, curses.color_pair(4) if self.error else curses.color_pair(3))
        self.screen.refresh()

    def draw_overview(self) -> None:
        status = self.data.get("status", {})
        universe = status.get("universe", {})
        rows = [
            ("Market", (status.get("market") or {}).get("source", "unknown")),
            ("Universe", "{} active symbols".format(len(universe.get("symbols") or []))),
            ("Jobs", json.dumps(status.get("jobs") or {}, sort_keys=True)),
            ("Agents", json.dumps(status.get("agent_tasks") or {}, sort_keys=True)),
            ("Trades", json.dumps(status.get("suggestions") or {}, sort_keys=True)),
            ("Triggers", status.get("active_triggers", 0)),
        ]
        self.write(5, 2, "SYSTEM", curses.A_BOLD | curses.color_pair(1))
        for row, (label, value) in enumerate(rows, 7):
            self.write(row, 4, "{:<12} {}".format(label, value))
        self.write(15, 2, "MANDATE", curses.A_BOLD | curses.color_pair(1))
        mandate = (status.get("config") or {}).get("mandate") or {}
        self.write(17, 4, "Themes  " + ", ".join(mandate.get("themes") or []))
        self.write(18, 4, "Regions " + ", ".join(mandate.get("regions") or []))
        self.write(19, 4, "Horizon " + str(mandate.get("time_horizon") or ""))

    def draw_agents(self) -> None:
        self.write(5, 2, "STATUS     ROLE          MODEL                         PROGRESS", curses.A_BOLD)
        for index, task in enumerate(self.data.get("agents", [])):
            row = index + 7
            style = curses.color_pair(2) if task["status"] == "complete" else curses.color_pair(3)
            if task["status"] == "failed": style = curses.color_pair(4)
            if index == min(self.selection, max(0, len(self.data.get("agents", [])) - 1)):
                style |= curses.A_REVERSE
            self.write(row, 2, "{:<10} {:<13} {:<29} {}".format(task["status"], task["role"], _clip(task.get("model") or "—", 28), task.get("progress") or task["objective"]), style)

    def draw_universe(self) -> None:
        universe = self.data.get("universe", {})
        symbols = universe.get("symbols") or []
        self.write(5, 2, "ACTIVE UNIVERSE · {} symbols".format(len(symbols)), curses.A_BOLD | curses.color_pair(1))
        self.write(7, 2, "  ".join(symbols), curses.A_BOLD)
        self.write(9, 2, "SNAPSHOTS", curses.A_BOLD | curses.color_pair(1))
        snapshots = universe.get("snapshots") or []
        for index, snapshot in enumerate(snapshots[:3]):
            self.write(
                10 + index,
                4,
                "{}  {:>4} candidates  {}".format(
                    str(snapshot.get("created_at") or "")[:19],
                    snapshot.get("candidate_count", 0),
                    snapshot.get("source") or "",
                ),
            )
        self.write(14, 2, "STATUS    SYMBOL   CONF   THEME / SUPERVISOR ASSESSMENT", curses.A_BOLD | curses.color_pair(1))
        for row, candidate in enumerate(universe.get("candidates") or [], 16):
            assessment = candidate.get("metadata", {}).get("supervisor_reason") or candidate.get("theme") or candidate.get("rationale")
            self.write(row, 2, "{:<9} {:<8} {:<6} {}".format(candidate["status"], candidate["symbol"], candidate["confidence"], assessment))

    def draw_events(self) -> None:
        self.write(5, 2, "TIME                 LEVEL    EVENT                    MESSAGE", curses.A_BOLD)
        for row, event in enumerate(self.data.get("events", []), 7):
            self.write(row, 2, "{}  {:<8} {:<24} {}".format(event["created_at"][:19], event["level"], event["event_type"], event["message"]), curses.color_pair(4) if event["level"] == "error" else 0)

    def draw_learning(self) -> None:
        self.write(5, 2, "STATUS       PARAMETER                  SHADOW EXPERIMENT", curses.A_BOLD)
        for row, item in enumerate(self.data.get("experiments", []), 7):
            self.write(row, 2, "{:<12} {:<26} {}".format(item["status"], _clip(item["parameter"], 25), item["proposal"]))

    def draw_trades(self) -> None:
        self.write(5, 2, "STATUS                    SYMBOL   SIDE   UPDATED", curses.A_BOLD)
        for row, item in enumerate(self.data.get("suggestions", []), 7):
            self.write(row, 2, "{:<25} {:<8} {:<6} {}".format(item["status"], item["symbol"], item["side"], item["updated_at"][:19]))


def run_tui(base_url: str, token: Optional[str] = None, *, conversation_id: Optional[str] = None, message: Optional[str] = None) -> None:
    client = ControlClient(base_url, token)
    conversation = conversation_id or "tui-{}".format(uuid.uuid4().hex[:8])
    if message is not None:
        _chat(client, conversation, message)
        return
    curses.wrapper(lambda screen: Dashboard(screen, client).run())
