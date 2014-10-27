# -*- coding: utf-8 -*-
"""Command management and processing."""
# Part of Atria MUD Server (https://github.com/whutch/atria)
# :copyright: (c) 2008 - 2014 Will Hutcheson
# :license: MIT (https://github.com/whutch/atria/blob/master/LICENSE.txt)

from .logs import get_logger
from .utils.exceptions import AlreadyExists
from .utils.mixins import HasFlags, HasWeaks


log = get_logger("commands")


class CommandManager:

    """A manager for command registration and control.

    This is a convenience manager and is not required for the server to
    function. All if its functionality can be achieved by subclassing,
    instantiating, and referencing commands directly.

    """

    def __init__(self):
        """Create a new command manager."""
        self._commands = {}

    def __contains__(self, command):
        return self._get_name(command) in self._commands

    def __getitem__(self, command):
        return self._commands[self._get_name(command)]

    @staticmethod
    def _get_name(command):
        if isinstance(command, type):
            return command.__name__
        else:
            return command

    def register(self, command):
        """Register a command.

        This method can be used to decorate a Command class.

        :param Command command: The command to be registered
        :returns Command: The registered command
        :raises AlreadyExists: If a command with that class name already exists
        :raises TypeError: If the supplied or decorated class is not a
                           subclass of Command.

        """
        if (not isinstance(command, type) or
                not issubclass(command, Command)):
            raise TypeError("must be subclass of Command to register")
        name = command.__name__
        if name in self._commands:
            raise AlreadyExists(name, self._commands[name], command)
        self._commands[name] = command
        return command


class Command(HasFlags, HasWeaks):

    """A command for performing actions through a shell."""

    # Whether this command receives its arguments un-parsed.
    no_parse = False

    def __init__(self, session, args):
        """Create a new command instance."""
        super(Command, self).__init__()
        self.session = session
        self.args = args

    @property
    def session(self):
        """Return the current session for this command."""
        return self._get_weak("session")

    @session.setter
    def session(self, new_session):
        """Set the current session for this command.

        :param _Session new_session: The session tied to this command
        :returns: None

        """
        self._set_weak("session", new_session)

    def execute(self):
        """Validate conditions and then perform this command's action."""
        if not self.session:
            return
        self._action()

    # noinspection PyMethodMayBeStatic
    def _action(self):
        """Do something; override this to add your functionality."""
        pass  # pragma: no cover


# We create a global CommandManager here for convenience, and while the server
# will generally only need one to work with, they are NOT singletons and you
# can make more CommandManager instances if you like.
COMMANDS = CommandManager()
