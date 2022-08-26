"""Module for reading the Dyson Sphere Program data files.

This requires the following files:
* ItemProtoSet.dat
* RecipeProtoSet.dat
* TechProtoSet.dat
* StringProtoSet.dat

All of these are extracted from the game file
"DSPGAME_Data/resources.assets", using a tool like Unity Asset Bundle
Extractor. To help locate them, they all have the type "MonoBehavior", at the
time of this writing they start at File ID 1/Path ID: 39345, and their names
all end with "ProtoSet". Another way to find them is that StringProtoSet is
one of the larger items in the bundle.

To use, call load_all(), which parses the files into a GameData object, or
call load_data() to load a single data type.
"""
# pylint: disable=too-few-public-methods,too-many-lines,unused-import

import collections
from contextlib import closing
from enum import IntEnum
import io
from os import path
from struct import unpack

__all__ = [
    'EItemType', 'ERecipeType',
    'ItemProto', 'ItemProtoSet',
    'RecipeProto', 'RecipeProtoSet',
    'StringProto', 'StringProtoSet',
    'load_all', 'load_data', 'do_all', 'find_all']

_DEBUG = False

class _Reader:
    """Reads binary data from a file stream.

    This class mostly just exposes read_fun() and tell_fun() as underlying
    functons to call, rather than performing reading itself. This is because
    of the inlining done by the _Codegen class, which reduces function calls
    to the minimum possible. (BufferedReader.read() is a native function,
    generally.)
    """
    __slots__ = ('buf_reader', 'read_fun', 'tell_fun')

    def __init__(self, filename, /):
        self.buf_reader = io.BufferedReader(io.FileIO(filename))
        self.tell_fun = self.buf_reader.tell
        if not _DEBUG:
            self.read_fun = self.buf_reader.read
        else:
            read_fun = self.buf_reader.read
            def print_wrapper(count=None):
                result = read_fun(count)
                print(f'Read {result!r}')
                return result
            self.read_fun = print_wrapper
        # Check if the file has Unity header bits, and skip them if so.
        # Takes advantage of the fact that peek returns the whole buffer.
        start = self.buf_reader.peek()
        # All our headers start with a 12-byte PPtr of 0:0 and a 1-byte
        # enabled field of true, and 3 padding bytes.
        if start[:16] == b'\0\0\0\0\0\0\0\0\0\0\0\0\x01\0\0\0':
            # Skip the above plus another 12-byte PPtr
            self.read_fun(28)
            _Codegen._read_base_string(self.read_fun)  # The name of the file

    def get_funcs(self):
        """Return the accessors"""
        return self.read_fun, self.tell_fun

    def close(self):
        """Close the reader"""
        self.buf_reader.close()


class _Codegen:
    #pylint: disable=no-self-use
    """Performs code generation for Object.

    This class contains methods that do not do actual stream parsing -
    instead, they return code snippets that will later perform the given
    parsing. This allows all the parsing to be inlined into a few large,
    dynamically-generated functions, cutting out almost all method call and
    lookup overhead. This makes a large difference given the branchy nature of
    the structures being parsed - it cuts the runtime by 35%.

    Almost all of the code snippets are expressions, which allows them to be
    recursively inlined into larger snippets by simply calling the appropriate
    function.

    Some code is too complicated to be done in an expression, and is
    implemented in an actual function. These are annotated with @staticmethed,
    and are off the common path.
    """

    def read_float(self):
        """Read a single-precision float"""
        return "unpack('f', read_fun(4))[0]"

    def read_double(self):
        """Read a double-precision float"""
        return "unpack('d', read_fun(8))[0]"

    def read_bool(self):
        """Helper for reading a single bool.

        Because of alignment, this is the same as an int."""
        return f'bool({self.read_int32()})'

    def read_int32(self):
        """Helper for reading a single int32"""
        return "int.from_bytes(read_fun(4), 'little', signed=True)"

    def read_int64(self):
        """Helper for reading a single int64"""
        return "int.from_bytes(read_fun(8), 'little', signed=True)"

    def read_string(self, name):
        """Read a base UTF-8 string

        This method is special, in that it is expected to return a series of
        statements, instead of an expression. (The calling code special-cases
        it.) The signature is different as a result, taking the "name"
        argument of the variable to set.
        """
        if _DEBUG:
            return f'    self.{name} = _Codegen._read_base_string(read_fun)'
        return f"""    slen = {self.read_int32()}
    self.{name} = read_fun(slen).decode()
    if slen & 3:
        read_fun(-slen & 3)"""

    @staticmethod
    def _read_base_string(read_fun):
        """Performs actual string reading, primarily in debug"""
        slen = int.from_bytes(read_fun(4), 'little', signed=True)
        if _DEBUG:
            print(f'String size: 0x{slen:X}')
        result = read_fun(slen).decode()
        if slen & 3:
            read_fun(-slen & 3)
        return result

    def read_enum(self, cls_name, /):
        """Read an enum, which is just a typed int"""
        return f'{cls_name}({self.read_int32()})'

    @staticmethod
    def read_array_real(clz, read_fun, tell_fun, /):
        """Performs actual array parsing, but not primarily used in non-debug"""
        alen = int.from_bytes(read_fun(4), 'little', signed=True)
        if _DEBUG:
            print(f'Array len: {alen} for {clz.__name__}')
        return [clz(read_fun, tell_fun) for x in range(alen)]

    def read_array(self, cls_name, /):
        """Read an array of objects"""
        if _DEBUG:
            return f'_Codegen.read_array_real({cls_name}, read_fun, tell_fun)'
        return f"[{cls_name}(read_fun, tell_fun) for i in range({self.read_int32()})]"

    def read_array_int32(self):
        """Read an array of int32s."""
        return f"[{self.read_int32()} for x in range({self.read_int32()})]"

    def read_array_double(self):
        """Read an array of double."""
        return f"[{self.read_double()} for x in range({self.read_int32()})]"

    def read_vector2f(self):
        """Read a pair of floats."""
        return f"({self.read_float()}, {self.read_float()})"

    def read_bad_type(self):
        """Used to check that a given class is never deserialized."""
        return "0; raise ValueError('Tried to parse unexpected type')"

    def generate_init(self, layout, cls_name, /):
        """Generates the dynamic __init__ code for the given class layout"""
        code = ["""def __init__(self, read_fun=None, tell_fun=None, /, **kwargs):
    if read_fun is None:"""]
        # Start with code to initialize the object as a tuple, including the
        # default constructor case.
        for name, typ in layout:
            if typ.startswith('string'):
                value = ''
            elif typ.startswith('array'):
                value = []
            elif typ.startswith('bool'):
                value = False
            else:
                value = 0
            code.append(f'        self.{name} = {value!r}')
        code.append("""        for k, v in kwargs.items():
            setattr(self, k, v)
        return""")
        # Otherwise, if there is a reader initialize from it.
        for name, typ in layout:
            if _DEBUG:
                code.append(
                    f"    print(f'@{{tell_fun():X}} {cls_name} {name}')")
            index = typ.index('(')
            method_name = typ[:index]
            method = getattr(self, 'read_' + method_name)
            args = []
            if index < len(typ) - 2:
                args.append(typ[index+1:-1])
            if method_name == 'string':
                code.append(method(name, *args))
            else:
                code.append(f'    self.{name} = ' + method(*args))
        return '\n'.join(code)

    def generate_do_all(self, layout, cls_name, /):
        """Generates the dynamic do_all code for the given class layout"""
        code = [f"""def do_all(self, fun, /):
    fun(self, {cls_name})"""]
        for name, typ in layout:
            if typ.startswith('object'):
                code.append(f"""    tmp = self.{name}
    if tmp:
        tmp.do_all(fun)""")
            elif typ.startswith('array('):
                code.append(f"""    arr = self.{name}
    if arr:
        for tmp in arr:
            if tmp:
                tmp.do_all(fun)""")
        return '\n'.join(code)


class Object:
    """Generic object base type that powers the rest of the type hierarchy.

    All subclasses of this are dumb struct types that contain no real logic;
    they simply describe their layout. This class sets up the code for
    each subclass by hooking __init_subclass__ so it can do parsing, __repr__,
    etc., without needing a full-blown metaclass.

    All methods besides __init_subclass__ are meant to be called on
    (all) subclasses.
    """

    def __init_subclass__(cls):
        # pylint: disable=exec-used,no-member
        """Generates code for subclasses"""
        layout = [x.strip().split(':', 1) for x in cls._layout.strip().split('\n')]
        for field in layout:
            # Append parens as needed, so that we get method calls later on.
            if not field[1][-1] == ')':
                field[1] += '()'
        cls.__slots__ = tuple(x[0] for x in layout)
        # We dynamically create this code, so that it will be compiled once
        # and then run at full speed.
        localz = {}
        exec(compile(_Codegen().generate_init(layout, cls.__name__) + '\n' +
                     _Codegen().generate_do_all(layout, cls.__name__),
                     f'<dynamic {cls.__name__} code>', 'exec'),
             globals(), localz)
        __init__ = localz['__init__']
        __init__.__qualname__ = f'{cls.__name__}.__init__'
        cls.__init__ = __init__
        do_all_fun = localz['do_all']
        do_all_fun.__qualname__ = f'{cls.__name__}.do_all'
        cls.do_all = do_all_fun
        # Precompute replacement string for speed
        fmt = ', '.join(x + '={!r}' for x in cls.__slots__)
        cls._repr_format = f'{cls.__name__}({fmt})'

        # We sort these to the front.
        front_attrs = ('id', 'name', 'description')
        str_attrs = [
            (front_attrs.index(v[0]) - 100 if v[0] in front_attrs else i,
                v[0], v[1]) for i,v in enumerate(layout)]
        str_attrs.sort()
        # Objects need to be recursively expanded with str(). Enums need to
        # use str() because repr() doesn't produce an expression which
        # evaluates to the value (which is against style). Lists are handled
        # specially, and will use str() because they contain Objects (or
        # ints). Everything else should use repr().
        str_attrs = [(x[1], x[1] + (
            '={!s}' if x[2].startswith('object') or x[2].startswith('enum')
            else '={!r}')) for x in str_attrs]
        cls._str_attrs = str_attrs
        cls._str_begin = f'{cls.__name__}('

    def __repr__(self):
        """Print all the attributes of the class.

        The result should be an expression that will round-trip back to the
        original result (assuming you did import * form dysonsphere),
        although it will probably be unreadably large in the complicated
        cases.
        """
        return self._repr_format.format(
                *[getattr(self, x) for x in self.__slots__])

    def __str__(self):
        """Print non-default attributes of the class.

        This skips printing all fields that have "default" values, i.e. that
        evaluate to False in a boolean context. So: None, 0, '', [], etc.
        Like repr(), this produces an expression that should produce the
        original result, modulo minor differences like where a string might
        have been ommitted entirely (None) and will be reconstructed as ''.
        """
        acc = []
        for attr, fmt in self._str_attrs:
            value = getattr(self, attr)
            if not value:
                continue
            if not isinstance(value, list):
                acc.append(fmt.format(value))
            else:
                # Special handling for arrays: This takes advantage of the
                # fact that it shares the same delimiter: ', '. Arrays are
                # always of either objects or ints, so either way we want to
                # recurse with str().
                sublist = [str(x) for x in value]
                sublist[0] = attr + '=[' + sublist[0]
                sublist[-1] += ']'
                acc.extend(sublist)
        return self._str_begin + ', '.join(acc) + ')'


class ERecipeType(IntEnum):
    """The type of a recipe."""
    NONE = 0
    SMELT = 1
    CHEMICAL = 2
    REFINE = 3
    ASSEMBLE = 4
    PARTICLE = 5
    EXCHANGE = 6
    PHOTON_STORE = 7
    FRACTIONATE = 8
    RESEARCH = 15


class EItemType(IntEnum):
    """The type of an item."""
    UNKNOWN = 0
    RESOURCE = 1
    MATERIAL = 2
    COMPONENT = 3
    PRODUCT = 4
    LOGISTICS = 5
    PRODUCTION = 6
    DECORATION = 7
    WEAPON = 8
    MATRIX = 9
    MONSTER = 10


class ItemProto(Object):
    """The data describing an individual item"""

    _layout = """
    name:string
    id:int32
    sid:string
    type:enum(EItemType)
    sub_id:int32
    mining_from:string
    produce_from:string
    stack_size:int32
    grade:int32
    upgrades:array_int32
    is_fluid:bool
    is_entity:bool
    can_build:bool
    build_in_gas:bool
    icon_path:string
    model_index:int32
    model_count:int32
    hp_max:int32
    ability:int32
    heat_value:int64
    potential:int64
    reactor_inc:float
    fuel_type:int32
    build_index:int32
    build_mode:int32
    grid_index:int32
    unlock_key:int32
    pre_tech_override:int32
    productive:bool
    mecha_material_id:int32
    desc_fields:array_int32
    description:string
    """


class ItemProtoSet(Object):
    """The data for all items in the game"""

    _layout = """
    table_name:string
    signature:string
    data_array:array(ItemProto)
    """


class RecipeProto(Object):
    """The data describing an individual recipe"""

    _layout = """
    name:string
    id:int32
    sid:string
    type:enum(ERecipeType)
    handcraft:bool
    explicit:bool
    time_spend:int32
    items:array_int32
    item_counts:array_int32
    results:array_int32
    result_counts:array_int32
    grid_index:int32
    icon_path:string
    description:string
    non_productive:bool
    """


class RecipeProtoSet(Object):
    """The data for all recipes in the game"""

    _layout = """
    table_name:string
    signature:string
    data_array:array(RecipeProto)
    """


class StringProto(Object):
    """A translation for anything at all"""

    _layout = """
    name:string
    id:int32
    sid:string
    zh_cn:string
    en_us:string
    fr_fr:string
    """


class StringProtoSet(Object):
    """All translations in the game"""

    _layout = """
    table_name:string
    signature:string
    data_array:array(StringProto)
    """


class TechProto(Object):
    """A translation for anything at all"""

    _layout = """
    name:string
    id:int32
    sid:string
    description:string
    conclusion:string
    published:bool
    level:int32
    max_level:int32
    level_coef1:int32
    level_coef2:int32
    icon_path:string
    is_lab_tech:bool
    pre_techs:array_int32
    pre_techs_implicit:array_int32
    pre_techs_max:bool
    items:array_int32
    item_points:array_int32
    property_override_items:array_int32
    property_item_counts:array_int32
    hash_needed:int64
    unlock_recipes:array_int32
    unlock_functions:array_int32
    unlock_values:array_double
    add_items:array_int32
    add_item_counts:array_int32
    position:vector2f
    """

class TechProtoSet(Object):
    """All techs and upgrades in the game"""

    _layout = """
    table_name:string
    signature:string
    data_array:array(TechProto)
    """


_ALL_DATA_TYPES = [
    ('ItemProtoSet', ItemProtoSet),
    ('RecipeProtoSet', RecipeProtoSet),
    ('StringProtoSet', StringProtoSet),
    ('TechProtoSet', TechProtoSet),
]

_VALID_TYPES = [x[0] for x in _ALL_DATA_TYPES]

def load_data(data_type, filename=None, /):
    """Load a single data file.

    The data_type is one of the filenames listed in the module docstring, but
    without '.dat'. The filename defaults the the data_type + '.dat', in the
    current directory, but can be overridden.
    """
    if data_type not in _VALID_TYPES:
        raise ValueError(
            f'{data_type!r} is not one of the valid types: {_VALID_TYPES}')
    if not filename:
        filename = data_type + '.dat'
    cls = _ALL_DATA_TYPES[_VALID_TYPES.index(data_type)][1]
    with closing(_Reader(filename)) as reader:
        return cls(*reader.get_funcs())

GameData = collections.namedtuple('GameData', _VALID_TYPES)
GameData.__doc__ = """namedtuple result type of load_all()"""

def load_all(root_dir='.', /):
    """Load all the data files into a GameData namedtuple.

    "root_dir" can be specified to load the data from somewhere else. If the
    files have non-standard names, use load_data() instead.

    The fields on the tuple have the same names as the data files, but without
    '.dat' - for instance events.dat is loaded into events."""
    result = dict((x, load_data(x, path.join(root_dir, x + '.dat')))
                  for x in _VALID_TYPES)
    return GameData(**result)

def do_all(obj, fun, /):
    """Apply the given fun recursively across obj and all subobjects.

    The data structures of Dyson Sphere Program are often deeply nested, which
    makes certain operations difficult. do_all() is optimized for traversing
    this object hierarchy more rapidly than a generic depth-first search, by
    taking advantage of the layout information that the types contain.

    "obj" must be a subtype of dysonsphere.Object, or an iterable of them, or
    a GameData.

    "fun" is a function taking two parameters - the object and its type. It
    will be called in a depth-first manner, visiting the nodes in "natural"
    order, i.e. the order they are declared.
    """
    if isinstance(obj, Object):
        obj.do_all(fun)
    elif isinstance(obj, GameData):
        for k, val in obj._asdict().items():
            if k == 'backers':
                continue
            for i in val:
                i.do_all(fun)
    else:  # Assume iterable
        for val in obj:
            val.do_all(fun)

def find_all(obj, cls, /):
    """Find all instances of cls recursively across obj.

    This uses do_all() to recursively search inside obj (including obj
    itself) for instances where the type is "cls". It will only work for
    subtypes of dysonsphere.Object, i.e. you can't find all ints this way.
    """
    acc = []
    def find_fun(res, res_cls, /):
        if res_cls == cls:
            acc.append(res)
    do_all(obj, find_fun)
    return acc
