# -*- coding: utf-8 -*-
"""Shell management and client input processing."""
# Part of Atria MUD Server (https://github.com/whutch/atria)
# :copyright: (c) 2008 - 2014 Will Hutcheson
# :license: MIT (https://github.com/whutch/atria/blob/master/LICENSE.txt)

from weakref import WeakValueDictionary

from .logs import get_logger
from .commands import Command
from .utils.exceptions import AlreadyExists
from .utils.funcs import joins
from .utils.mixins import HasFlags, HasParent


log = get_logger("shells")


class STATES:

    """A collection of values to describe the state a shell is in.

    This is a placeholder for a more sophisticated solution later.

    """

    connected = 0
    login = 1
    playing = 2


class ShellManager:

    """A manager for shell registration.

    This is a convenience manager and is not required for the server to
    function. All of its functionality can be achieved by subclassing,
    instantiating, and referencing shells directly.

    """

    def __init__(self):
        """Create a new shell manager."""
        self._shells = {}

    def __contains__(self, shell):
        return shell in self._shells

    def __getitem__(self, shell):
        return self._shells[shell]

    def register(self, shell):
        """Register a shell.

        This method can be used to decorate a Shell class.

        :param Shell shell: The shell to be registered
        :returns Shell: The registered shell
        :raises AlreadyExists: If a shell with that class name already exists
        :raises TypeError: If the supplied or decorated class is not a
                           subclass of Shell.

        """
        if not isinstance(shell, type) or not issubclass(shell, Shell):
            raise TypeError("must be subclass of Shell to register")
        name = shell.__name__
        if name in self._shells:
            raise AlreadyExists(name, self._shells[name], shell)
        self._shells[name] = shell
        return shell


class Shell(HasFlags, HasParent):

    """A shell for processing client input."""

    state = STATES.connected

    # If you want to add or remove verbs from a subclass of Shell, you must
    # redefine your own _verbs attribute as a new WeakValueDictionary. The
    # verbs of the parent class will still be accessible with find_verb
    # because it iterates through the shell lineage.
    _verbs = WeakValueDictionary()

    # Delimiters should be a pair of equal-length strings that contain
    # opening and closing delimiter characters. A delimiter at any given index
    # in the first string will be the opening delimiter that will pair with a
    # closing delimiter at the same index in the second string. This allows
    # shells to delimit arguments using non-equal pairs such as braces,
    # brackets, and parentheses.
    delimiters = ("\"'`", "\"'`")

    def __init__(self):
        """Create a new shell."""
        super().__init__()

    @property
    def session(self):
        """Return the current session for this shell."""
        return self._get_weak("session")

    @session.setter
    def session(self, new_session):
        """Set the current session for this shell.

        If ``new_session`` is not None, this shell's init method
        will be called.

        :param _Session new_session: The session tied to this shell
        :returns: None

        """
        self._set_weak("session", new_session)
        if new_session is not None:
            self.init()

    # noinspection PyMethodMayBeStatic
    def init(self):
        """Initialize this shell for the session.

        This method is called when the shell is assigned; override it to do
        anything prior to the initial prompt.

        """
        return

    # noinspection PyMethodMayBeStatic
    def get_prompt(self):
        """Generate the current prompt for this shell."""
        return "^y>^~ "

    @classmethod
    def inherited_verbs(cls):
        """Return whether this shell inherited its verbs through subclassing.

        This is an important distinction, as you cannot add or remove verbs
        from a shell that inherited its verb store, as that would affect the
        store of the parent and any of that parent's other subclasses that
        also inherit the store.

        """
        if cls is Shell or cls._verbs is None:
            return False
        # noinspection PyUnresolvedReferences,PyProtectedMember
        if super(cls, cls)._verbs is cls._verbs:
            # We have the same verb store as our parent class.
            return True
        # We must have declared our own, good on us.
        return False

    @staticmethod
    def _validate_verb(verb):
        if not verb or not isinstance(verb, str):
            raise ValueError(joins("invalid verb:", repr(verb)))
        if len(verb) == 1:
            # This is a shortcut verb, it can't be a letter
            if verb.isalpha():
                raise ValueError("single-character verbs cannot be letters")
        else:
            if not verb.isalpha():
                raise ValueError("verbs can only contain letters")

    @classmethod
    def add_verbs(cls, command, *verbs):
        """Add verbs to this shell that trigger a given command.

        :param Command command: The command that will be executed
        :param str verbs: A sequence of verbs that trigger the command
        :returns: None
        :raises KeyError: If this shell inherited its verb store
        :raises TypeError: If the given command is not a Command subclass
        :raises ValueError: If any of the verbs are not valid verbs

        """
        if cls.inherited_verbs():
            raise KeyError("cannot add verbs without explicit verb store")
        if not isinstance(command, type) or not issubclass(command, Command):
            raise TypeError("cannot add verbs for non-Command class")
        for verb in verbs:
            cls._validate_verb(verb)
            if verb in cls._verbs:
                raise AlreadyExists(verb, cls._verbs[verb], command)
        for verb in verbs:
            cls._verbs[verb.lower()] = command

    @classmethod
    def remove_verbs(cls, *verbs):
        """Remove verbs from this shell.

        :param str verbs: A sequence of verbs to remove
        :returns: None
        :raises KeyError: If this shell inherited its verb store

        """
        if cls.inherited_verbs():
            raise KeyError("cannot remove verbs without explicit verb store")
        for verb in verbs:
            if verb in cls._verbs:
                del cls._verbs[verb]

    @classmethod
    def get_command(cls, verb):
        """Get a command in this shell by its verb.

        :param str verb: The verb of the command to get
        :returns Command|None: The command with that verb or None

        """
        return cls._verbs.get(verb)

    def find_command(self, verb):
        """Find a command in this shell's lineage by its verb.

        Will return the first command found, as multiple stores may have
        different commands using the same verb.

        :param str verb: The verb of the command to search for
        :returns Command|None: The command with that verb or None

        """
        for shell in self.get_lineage():
            command = shell.get_command(verb)
            if command:
                return command
        return None

    @classmethod
    def _one_argument(cls, data):
        """Parse a single argument from data.

        This always returns exactly two values; if there is no remaining data
        after parsing one argument, the second value will be an empty string.
        If there was no data worth parsing, both values will be empty strings.

        :param str data: The data to get an argument from.
        :returns str,str: The parsed argument and any remaining data

        """
        # Dump leading whitespace.
        data = data.lstrip()
        # Is there anything left to parse?
        if not data:
            return "", ""
        if data[0] in cls.delimiters[0]:
            # This is a delimited string, so read until it ends or data does.
            delimiter = cls.delimiters[0].index(data[0])
            delimiter_end = cls.delimiters[1][delimiter]
            closed = False
            try:
                # Does this delimited string have a closing delimiter?
                end = data.index(delimiter_end, 1)
                closed = True
            except ValueError:
                # No it doesn't, so read everything.
                end = len(data)
            arg = data[1:end]
            if closed:
                end += 1
            if not arg:
                # It was an empty delimited string, start over and
                # look for a new argument.
                return cls._one_argument(data[end:])
        else:
            # Not a delimited string, so read until whitespace or a delimiter.
            end = 1
            data_end = len(data)
            stop_on = " \n\r\t" + cls.delimiters[0]
            while end < data_end:
                if data[end] in stop_on:
                    break
                end += 1
            arg = data[:end]
        # One way or another, we found an argument.
        return arg, data[end:]

    @classmethod
    def _iter_arguments(cls, data):
        while data:
            arg, data = cls._one_argument(data)
            if arg:
                yield arg

    @classmethod
    def _get_arguments(cls, data, max_args=-1):
        """Parse data into a list of arguments.

        Any un-parsed arguments (either because max was reached or because a
        delimiter was opened and not closed) will be returned as the last
        element of the resulting list.

        :param str data: The data to be broken down into arguments
        :param int max_args: The maximum number of arguments to parse,
                             if less than zero, all arguments are parsed
        :returns list: The parsed arguments

        """
        args = []
        while data and max_args != 0:
            arg, data = cls._one_argument(data)
            if arg:
                args.append(arg)
            if max_args > 0:
                max_args -= 1
        if data:
            args.append(data)
        return args

    def parse(self, data):
        """Parse input from the client session.

        :param str data: The data to be parsed
        :returns: None

        """
        if not data:
            return
        command = None
        if not data[0].isalpha():
            # Check for verb shortcuts
            command = self.find_command(data[0])
        if command:
            # We found a shortcut, everything else is args
            data = data[1:]
        else:
            # No shortcut, so find a verb
            arg, data = self._one_argument(data)
            command = self.find_command(arg)
        if command:
            if command.no_parse:
                # Let this command do its own argument parsing
                args = [data]
            else:
                args = self._get_arguments(data)
            try:
                # noinspection PyCallingNonCallable
                instance = command(self.session, args)
                instance.execute()
            except:
                # To be expanded later with some checking and logging
                raise
        else:
            self.session.send("Huh?")


# We create a global ShellManager here for convenience, and while the server
# will generally only need one to work with, they are NOT singletons and you
# can make more ShellManager instances if you like.
SHELLS = ShellManager()


# This is an example shell that overrides parse and sets a higher state
@SHELLS.register
class EchoShell(Shell):

    """A simple shell that echos back anything the client sends."""

    state = STATES.playing

    def parse(self, data):
        """Echo any input back to the client.

        :param str data: Input from the client

        """
        if data.strip() == "quit":
            self.session.close("Okay, goodbye!",
                               log_msg=joins(self.session, "has quit"))
        else:
            self.session.send("You sent:", data)
