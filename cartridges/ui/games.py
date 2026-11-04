# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2025 Zoey Ahmed
# SPDX-FileCopyrightText: Copyright 2025 kramo
# SPDX-FileCopyrightText: Copyright 2025 Jamie Gravendeel

import locale
import logging
from collections.abc import Callable
from gettext import gettext as _
from typing import TYPE_CHECKING, Any, cast

from gi.repository import Gio, GLib, GObject, Gtk

from cartridges import STATE_SETTINGS, sources
from cartridges.games import Game
from cartridges.sources import imported

from . import closures

if TYPE_CHECKING:
    from .window import Window

_SORT_MODES = {
    "last_played": ("last-played", True),
    "a-z": ("name", False),
    "z-a": ("name", True),
    "newest": ("added", True),
    "oldest": ("added", False),
}

_launch_monitors: set[int] = set()


class GameActions(Gio.SimpleActionGroup):
    """Action group for game actions."""

    __gtype_name__ = __qualname__

    game = GObject.Property(type=Game)

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

        self.add_action_entries((
            ("add", lambda *_: add()),
            ("edit", lambda *_: edit(self.game)),
            ("play", lambda *_: play(self.game)),
            ("hide", lambda *_: hide(self.game)),
            ("unhide", lambda *_: unhide(self.game)),
            ("remove", lambda *_: remove(self.game)),
        ))

        game = Gtk.PropertyExpression.new(GameActions, None, "game")
        has_game = Gtk.ClosureExpression.new(bool, closures.bool_, (game,))
        hidden = Gtk.PropertyExpression.new(Game, game, "hidden")
        not_hidden = Gtk.ClosureExpression.new(bool, closures.not_, (hidden,))
        removed = Gtk.PropertyExpression.new(Game, game, "removed")
        not_removed = Gtk.ClosureExpression.new(bool, closures.not_, (removed,))
        false = Gtk.ConstantExpression.new_for_value(False)

        edit_action = cast(Gio.SimpleAction, self.lookup_action("edit"))
        play_action = cast(Gio.SimpleAction, self.lookup_action("play"))
        hide_action = cast(Gio.SimpleAction, self.lookup_action("hide"))
        unhide_action = cast(Gio.SimpleAction, self.lookup_action("unhide"))
        remove_action = cast(Gio.SimpleAction, self.lookup_action("remove"))

        has_game.bind(edit_action, "enabled", self)
        has_game.bind(play_action, "enabled", self)
        Gtk.TryExpression.new((hidden, false)).bind(unhide_action, "enabled", self)
        Gtk.TryExpression.new((not_hidden, false)).bind(hide_action, "enabled", self)
        Gtk.TryExpression.new((not_removed, false)).bind(remove_action, "enabled", self)


class GameEditable(GObject.Object):
    """A helper object for editing a game."""

    __gtype_name__ = __qualname__

    game = GObject.Property(type=Game)

    valid = GObject.Property(type=bool, default=False)

    executable = GObject.Property(type=str)
    name = GObject.Property(type=str)
    developer = GObject.Property(type=str)

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

        executable = Gtk.PropertyExpression.new(GameEditable, None, "executable")
        name = Gtk.PropertyExpression.new(GameEditable, None, "name")
        valid = Gtk.ClosureExpression.new(bool, closures.all_, (executable, name))
        valid.bind(self, "valid", self)

    def apply(self) -> bool:
        """Save and apply the changes, leaving failed drafts available."""
        if not self.valid:
            return False

        source = sources.get(imported.ID) if self.game is None else None
        game = self.game if self.game is not None else imported.new(source)
        if not _save_changes(
            game, executable=self.executable, name=self.name, developer=self.developer
        ):
            return False
        if source is not None:
            self.game = game
            source.append(game)
        return True


def _save_changes(
    game: Game, *, retry: Callable[[], bool] | None = None, **changes: Any
) -> bool:
    old_name = game.name
    try:
        game.save_changes(**changes)
    except (OSError, TypeError, ValueError) as error:
        logging.getLogger(__name__).warning(
            "Could not save game changes (%s)", type(error).__name__
        )
        _window().send_toast(_("Could not save changes"), undo=retry)
        return False
    if game.name != old_name:
        sorter.changed(Gtk.SorterChange.DIFFERENT)
    return True


def add():
    """Add a new game."""
    window = _window()
    if window.navigation_view.props.visible_page_tag != "details":
        window.navigation_view.push_by_tag("details")
    window.details.add()


def edit(game: Game):
    """Edit `game`."""
    window = _window()
    window.details.game = game
    window.navigation_view.push_by_tag("details")
    window.details.edit()


def play(game: Game):
    """Submit a launch request and observe its command without waiting."""
    name = game.name
    try:
        process = game.play()
    except (OSError, ValueError) as error:
        logging.getLogger(__name__).warning(
            "Could not launch game (%s)", type(error).__name__
        )
        _launch_toast(_("Could not launch {}.").format(name))
        return

    def poll() -> bool:
        status = process.poll()
        if status is None:
            return GLib.SOURCE_CONTINUE
        _launch_monitors.discard(source_id)
        if status:
            logging.getLogger(__name__).warning("Launch command exited (%s)", status)
            _launch_toast(
                _("Launch command for {} exited unsuccessfully.").format(name)
            )
        return GLib.SOURCE_REMOVE

    source_id = GLib.timeout_add(250, poll)
    _launch_monitors.add(source_id)


def stop_launch_monitors():
    """Stop observing commands without waiting for or terminating them."""
    for source_id in _launch_monitors:
        GLib.source_remove(source_id)
    _launch_monitors.clear()


def _launch_toast(message: str):
    app = Gio.Application.get_default()
    if app and (window := cast(Gtk.Application, app).props.active_window):
        cast("Window", window).send_toast(message)


def hide(game: Game):
    """Hide `game` and notify the user with a toast."""
    _set_visibility(game, "hidden", True, _("{} hidden").format(game.name))


def unhide(game: Game):
    """Unhide `game` and notify the user with a toast."""
    _set_visibility(game, "hidden", False, _("{} unhidden").format(game.name))


def remove(game: Game):
    """Remove `game` and notify the user with a toast."""
    _set_visibility(game, "removed", True, _("{} removed").format(game.name))


def _set_visibility(game: Game, prop: str, value: bool, message: str):
    previous = getattr(game, prop)
    if not _save_changes(game, **{prop: value}):
        return

    def undo() -> bool:
        return _save_changes(game, retry=undo, **{prop: previous})

    _window().send_toast(message, undo=undo)


def _window() -> "Window":
    app = cast(Gtk.Application, Gio.Application.get_default())
    return cast("Window", app.props.active_window)


def _sort(game1: Game, game2: Game) -> int:
    prop, invert = _SORT_MODES[STATE_SETTINGS.get_string("sort-mode")]
    a = (game2 if invert else game1).get_property(prop)
    b = (game1 if invert else game2).get_property(prop)

    return (
        _name_cmp(a, b)
        if isinstance(a, str)
        else ((a > b) - (a < b)) or _name_cmp(game1.name, game2.name)
    )


def _name_cmp(a: str, b: str) -> int:
    a, b = (name.lower().removeprefix("the ") for name in (a, b))
    return locale.strcoll(a, b)


filter_ = Gtk.EveryFilter()
filter_.append(
    Gtk.BoolFilter(
        expression=Gtk.PropertyExpression.new(Game, None, "removed"),
        invert=True,
    )
)
filter_.append(
    Gtk.BoolFilter(
        expression=Gtk.PropertyExpression.new(Game, None, "blacklisted"),
        invert=True,
    )
)

sorter = Gtk.CustomSorter.new(lambda game1, game2, _: _sort(game1, game2))
STATE_SETTINGS.connect(
    "changed::sort-mode", lambda *_: sorter.changed(Gtk.SorterChange.DIFFERENT)
)

model = Gtk.SortListModel(
    model=Gtk.FilterListModel(
        model=Gtk.FlattenListModel(model=sources.model),
        filter=filter_,
        watch_items=True,
    ),
    sorter=sorter,
)
