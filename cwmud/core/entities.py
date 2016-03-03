# -*- coding: utf-8 -*-
"""Entities, the base of all complex MUD objects."""
# Part of Clockwork MUD Server (https://github.com/whutch/cwmud)
# :copyright: (c) 2008 - 2016 Will Hutcheson
# :license: MIT (https://github.com/whutch/cwmud/blob/master/LICENSE.txt)

from copy import deepcopy
from weakref import WeakValueDictionary

from pylru import lrucache

from .attributes import Attribute, DataBlob
from .logs import get_logger
from .timing import TIMERS
from .utils.exceptions import AlreadyExists
from .utils.funcs import class_name, int_to_base_n, joins
from .utils.mixins import (HasFlags, HasFlagsMeta, HasTags,
                           HasWeaks, HasWeaksMeta)


log = get_logger("entities")


# Do NOT change these after your server has started generating UIDs or you
# risk running into streaks of duplicate UIDs.
_uid_timecode_multiplier = 10000
_uid_timecode_charset = ("0123456789aAbBcCdDeEfFgGhHijJkKLmM"
                         "nNopPqQrRstTuUvVwWxXyYzZ")
# I left out "I", "l", "O", and "S" to make time codes easier to distinguish
# regardless of font.  If my base 58 math is to be believed, this character set
# should generate eight-digit time codes with 100 microsecond precision until
# October 25th, 2375, and then nine-digit codes well into the 26th millennium.


class EntityManager:

    """A manager for entity types."""

    def __init__(self):
        """Create a new entity manager."""
        self._entities = {}

    def __contains__(self, name):
        return name in self._entities

    def __getitem__(self, name):
        return self._entities[name]

    def register(self, entity):
        """Register an entity type.

        This method can be used to decorate an Entity class.

        :param Entity entity: The entity to be registered
        :returns Entity: The registered entity
        :raises AlreadyExists: If an entity with that class name already exists
        :raises TypeError: If the supplied or decorated class is not a
                           subclass of Entity

        """
        if (not isinstance(entity, type) or
                not issubclass(entity, Entity)):
            raise TypeError("must be subclass of Entity to register")
        name = entity.__name__
        if name in self._entities:
            raise AlreadyExists(name, self._entities[name], entity)
        self._entities[name] = entity
        return entity

    def save(self):
        """Save the dirty instances of all registered entities."""
        count = 0
        for entity in self._entities.values():
            # noinspection PyProtectedMember
            for instance in entity._instances.values():
                if instance.is_savable and instance.is_dirty:
                    instance.save()
                    count += 1
        if count:
            log.debug("Saved %s dirty entities.", count)


# noinspection PyDocstring
class _EntityMeta(HasFlagsMeta, HasWeaksMeta):

    def __init__(cls, name, bases, namespace):
        super().__init__(name, bases, namespace)
        cls._base_blob = type(name + "BaseBlob", (DataBlob,), {})
        cls._instances = WeakValueDictionary()
        cls._caches = {}
        # noinspection PyUnresolvedReferences
        cls.register_cache(cls.get_key_name())

    def register_blob(cls, name):
        """Decorate a data blob to register it on this entity.

        :param str name: The name of the field to store the blob
        :returns DataBlob: The decorated blob
        :raises AlreadyExists: If the given name already exists as an attr
        :raises TypeError: If the supplied or decorated class is not a
                           subclass of DataBlob

        """
        if hasattr(cls, name):
            raise AlreadyExists(name, getattr(cls, name))

        # noinspection PyProtectedMember
        def _inner(blob_class):
            if (not isinstance(blob_class, type) or
                    not issubclass(blob_class, DataBlob)):
                raise TypeError("must be subclass of DataBlob to register")
            # noinspection PyUnresolvedReferences
            cls._base_blob._blobs[name] = blob_class
            prop = property(lambda s: s._base_blob._blobs[name])
            setattr(cls, name, prop)
            return blob_class

        return _inner

    def register_attr(cls, name):
        """Decorate an attribute to register it on this entity.

        :param str name: The name of the field to store the attribute
        :returns Attribute: The decorated attribute
        :raises AlreadyExists: If the given name already exists as an attr
        :raises TypeError: If the supplied or decorated class is not a
                           subclass of Attribute

        """
        if hasattr(cls, name):
            raise AlreadyExists(name, getattr(cls, name))

        # noinspection PyProtectedMember
        def _inner(attr_class):
            if (not isinstance(attr_class, type) or
                    not issubclass(attr_class, Attribute)):
                raise TypeError("must be subclass of Attribute to register")
            # noinspection PyUnresolvedReferences
            cls._base_blob._attrs[name] = attr_class
            getter = lambda s: s._base_blob._get_attr_val(name)
            setter = (lambda s, v: s._base_blob._set_attr_val(name, v)
                      if not attr_class._read_only else None)
            setattr(cls, name, property(getter, setter))
            return attr_class

        return _inner

    @staticmethod
    def _cache_eject_callback(key, entity):
        """A callback for when entities are ejected from a cache.

        When an entity is dumped from all of it's caches, there's a chance
        it could fall out of scope before every being saved, so we save it
        on ejection to be sure.

        :param key: The ejected entity's cache key
        :param Entity entity: The ejected entity
        :return None:

        """
        entity.save()

    def register_cache(cls, key, size=512):
        """Create a new cache for this entity, keyed by attribute.

        :param str key: The attribute name to use as a key
        :param int size: The size of the cache to create
        :returns None:
        :raises KeyError: If a cache already exists for `key`

        """
        if key in cls._caches:
            raise KeyError(joins("entity already has cache:", key))
        cls._caches[key] = lrucache(size, cls._cache_eject_callback)


class Entity(HasFlags, HasTags, HasWeaks, metaclass=_EntityMeta):

    """The base of all persistent objects in the game."""

    _store = None
    _store_key = "uid"
    _uid_code = "E"

    type = "entity"

    # These are overridden in the metaclass, I just put them here
    # to avoid a lot of unresolved reference errors in IDE introspection.
    _base_blob = None
    _instances = None
    _caches = None

    __uid_timecode = 0  # Used internally for UID creation.

    def __init__(self, data=None, active=False, savable=True):
        super().__init__()

        def _build_base_blob(cls, blob=self._base_blob(self), checked=set()):
            # Recursively update our base blob with the blobs of our parents.
            for base in cls.__bases__:
                _build_base_blob(base)
                # We don't need to do anything with the blob returned by this
                # because we're abusing the mutability of default arguments.
            if issubclass(cls, Entity):
                if cls not in checked:
                    # noinspection PyProtectedMember
                    blob._update(cls._base_blob(self))
                    checked.add(cls)
            return blob

        self._base_blob = _build_base_blob(self.__class__)
        self._dirty = False
        self._savable = savable
        # Never, ever manually change an object's UID! There are no checks
        # for removing the old UID from the store, updating UID links, or
        # anything else like that.  Bad things will happen!
        self._uid = None

        # An active entity is considered "in play", inactive entities are
        # hidden from the game world.
        self.active = active

        if data is not None:
            self.deserialize(data)
        if self._uid is None:
            self._uid = self.make_uid()
        self._instances[self._uid] = self
        cache = self._caches.get(self.get_key_name())
        if cache is not None and self.key not in cache:
            cache[self.key] = self

    def __repr__(self):
        return joins("Entity<", self.uid, ">", sep="")

    @property
    def uid(self):
        """Return this entity's UID."""
        return self._uid

    @property
    def key(self):
        """Return the value of this entity's storage key."""
        if len(self._store_key) == 3:
            getter = self._store_key[1]
            if callable(getter):
                return getter(self)
        return getattr(self, self._store_key)

    @key.setter
    def key(self, new_key):
        """Set this entity's storage key.

        :param any new_key: The new key
        :returns None:

        """
        if len(self._store_key) == 3:
            setter = self._store_key[2]
            if callable(setter):
                setter(self, new_key)
                return
        setattr(self, self._store_key, new_key)

    @property
    def is_dirty(self):
        """Return whether this entity is dirty and needs to be saved."""
        return self._dirty

    @property
    def is_savable(self):
        """Return whether this entity can be saved."""
        return self._store and self._savable

    @classmethod
    def get_key_name(cls):
        """Return the name of this entity's storage key."""
        if len(cls._store_key) == 3:
            getter, setter = cls._store_key[1:]
            if callable(getter) and callable(setter):
                return cls._store_key[0]
        return cls._store_key

    def _flags_changed(self):
        self.dirty()

    def _tags_changed(self):
        self.dirty()

    def dirty(self):
        """Mark this entity as dirty so that it will be saved."""
        self._dirty = True

    def serialize(self):
        """Create a sanitized dict from the data on this entity.

        :returns dict: The serialized data

        """
        data = self._base_blob.serialize()
        data["type"] = class_name(self)
        data["uid"] = self._uid
        data["flags"] = self.flags.as_tuple
        data["tags"] = deepcopy(self.tags.as_dict)
        return data

    def deserialize(self, data):
        """Update this entity's data using values from a dict.

        :param dict data: The data to deserialize
        :returns None:

        """
        if "type" in data:
            del data["type"]
        if "uid" in data:
            self._uid = data.pop("uid")
        if "flags" in data:
            self.flags.add(*data.pop("flags"))
        if "tags" in data:
            self.tags.clear()
            self.tags.update(data.pop("tags"))
        self._base_blob.deserialize(data)

    @classmethod
    def reconstruct(cls, data):
        """Reconstruct an entity from a dict of its data.

        The given `data` must include a "type" key with the name of a
        registered Entity class as its value.

        This differs from the deserialize method in that this method will
        return an entity created from a class specified in the data, rather
        than merging the data into an existing instance of a (potentially
        different) class.

        :param dict data: The data to reconstruct the entity from
        :returns Entity: The reconstructed entity instance
        :raises KeyError: If `data` has no "type" key or the value of the
                          given key is not a registered Entity class

        """
        entity_name = data.pop("type", None)
        if not entity_name or entity_name not in ENTITIES:
            raise KeyError("failed to reconstruct entity: bad class key")
        return ENTITIES[entity_name](data)

    @classmethod
    def make_uid(cls):
        """Create a UID for this entity.

        UIDs are in the form "C-TTTTTTTT", where C is the entity code and T
        is the current time code.  (Ex. "E-6jQZ4zvH")

        :returns str: The new UID

        """
        big_time = TIMERS.time * _uid_timecode_multiplier
        if big_time > Entity.__uid_timecode:
            Entity.__uid_timecode = big_time
        else:
            Entity.__uid_timecode += 1
        timecode_string = int_to_base_n(Entity.__uid_timecode,
                                        _uid_timecode_charset)
        uid = "-".join((cls._uid_code, timecode_string))
        return uid

    @classmethod
    def exists(cls, key):
        """Check if an entity with the given key exists.

        :param key: The key the entity's data is stored under
        :returns bool: True if it exists, else False

        """
        # Check the store first.
        if cls._store and cls._store.has(key):
            return True
        # Then check unsaved instances.
        if cls.get_key_name() == "uid":
            if key in cls._instances:
                return True
        else:
            # This entity isn't saved by UID, so we have to check
            # each one for a matching store key.
            for entity in cls._instances.values():
                if entity.key == key:
                    return True
        return False

    @classmethod
    def find(cls, *attr_value_pairs, cache=True, store=True, match=all, n=0):
        """Find one or more entities by one of their attribute values.

        :param iterable attr_value_pairs: Pairs of attributes and values to
                                          match against; unless _or is True,
                                          they must all match
        :param bool cache: Whether to check the _instances cache
        :param bool store: Whether to check the store
        :param function match: Function to test if an entity matches, given
                               a list of booleans returned by attr/value
                               comparisons; should be any or all
        :param int n: The maximum number of matches to return
        :returns list: A list of found entities, if any
        :raises ValueError: If both `store_only` and `cache_only` are True

        """
        pairs = []
        while attr_value_pairs:
            attr, value, *attr_value_pairs = attr_value_pairs
            pairs.append((attr, value))
        found = set()
        checked_keys = set()
        if cache:
            # Check the cache.
            for entity in cls._instances.values():
                matches = [getattr(entity, _attr) == _value
                           for _attr, _value in pairs]
                if match(matches):
                    found.add(entity)
                    if n and len(found) >= n:
                        break
                checked_keys.add(entity.key)
        if store and (not n or (n and len(found) < n)):
            # Check the store.
            for key in cls._store.keys():
                if key in checked_keys:
                    # We already checked this entity when we were checking the
                    # cache, so don't bother reading from the store.
                    continue
                try:
                    data = cls._store.get(key)
                except KeyError:
                    # This key is pending deletion.
                    data = None
                if data:
                    matches = [data.get(_attr) == _value
                               for _attr, _value in pairs]
                    if match(matches):
                        entity = cls.reconstruct(data)
                        entity._dirty = False
                        found.add(entity)
                        if n and len(found) >= n:
                            break
        if n == 1:
            return found.pop() if found else None
        else:
            return list(found)

    @classmethod
    def all(cls):
        """Return all active instances of this entity.

        :returns list: All active instances of this entity type

        """
        return [instance for instance in cls._instances.values()
                if instance.active]

    @classmethod
    def load(cls, key, from_cache=True, default=KeyError):
        """Load an entity from storage.

        If `from_cache` is True and an instance is found in the _instances
        cache then the found instance will be returned as-is and NOT
        reloaded from the store.  If you want to reset an entity's data to a
        stored state, use the revert method instead.

        :param key: The key the entity's data is stored under
        :param bool from_cache: Whether to check the _instances cache for a
                                match before reading from storage
        :param default: A default value to return if no entity is found; if
                        default is an exception, it will be raised instead
        :returns Entity: The loaded entity or default

        """
        cache = cls._caches.get(cls.get_key_name())

        def _find():
            if from_cache:
                key_name = cls.get_key_name()
                if key_name == "uid" and key in cls._instances:
                    return cls._instances[key]
                if cache is not None and key in cache:
                    return cache[key]
                if key_name != "uid":
                    # This is probably slow and may not be worth it.
                    for entity in cls._instances.values():
                        if entity.key == key:
                            return entity
            if cls._store:
                data = cls._store.get(key, default=None)
                if data:
                    if "uid" not in data:
                        log.warn("No uid for %s loaded with key: %s!",
                                 class_name(cls), key)
                    entity = cls.reconstruct(data)
                    entity._dirty = False
                    return entity

        found = _find()
        if found:
            if cache is not None and key not in cache:
                cache[key] = found
            return found
        # Nothing was found.
        if isinstance(default, type) and issubclass(default, Exception):
            raise default(key)
        else:
            return default

    def save(self):
        """Store this entity."""
        if not self.is_savable:
            log.warn("Tried to save non-savable entity %s!", self)
            return
        if "_old_key" in self.tags:
            # The entity's key has changed, so we need to handle that.
            old_key = self.tags["_old_key"]
            if self._store.has(old_key):
                self._store.delete(old_key)
            del self.tags["_old_key"]
        data = self.serialize()
        self._store.put(self.key, data)
        self._dirty = False

    def revert(self):
        """Revert this entity to a previously saved state."""
        if not self._store:
            raise TypeError("cannot revert entity with no store")
        data = self._store.get(self.key)
        if self.uid != data["uid"]:
            raise ValueError(joins("uid mismatch trying to revert", self))
        self.deserialize(data)
        self._dirty = False

    def clone(self, new_key):
        """Create a new entity with a copy of this entity's data.

        :param new_key: The key the new entity will be stored under;
                        new_key can be callable, in which case the return
                        value will be used as the key
        :returns Entity: The new, cloned entity

        """
        if not self._store:
            raise TypeError("cannot clone entity with no store")
        entity_class = type(self)
        if callable(new_key):
            new_key = new_key()
        if self._store.has(new_key):
            raise KeyError(joins("key exists in entity store:", new_key))
        data = self.serialize()
        del data["uid"]
        new_entity = entity_class(data)
        new_entity.key = new_key
        return new_entity

    def delete(self):
        """Delete this entity from its store."""
        cache = self._caches.get(self.get_key_name())
        if cache and self.key in cache:
            del cache[self.key]
        if self._store and self._store.has(self.key):
            self._store.delete(self.key)


# We create a global EntityManager here for convenience, and while the
# server will generally only need one to work with, they are NOT singletons
# and you can make more EntityManager instances if you like.
ENTITIES = EntityManager()


@Entity.register_attr("version")
class EntityVersion(Attribute):

    """An entity's version."""

    default = 1

    @classmethod
    def validate(cls, entity, new_value):
        if not isinstance(new_value, int):
            raise TypeError("entity version must be a number")
        return new_value
