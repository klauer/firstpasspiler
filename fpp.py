import collections
import inspect
import pathlib
import re
import textwrap

import inflection

import clang
import clang.cindex
from clang.cindex import CursorKind, TokenKind


def debug(cursor):
    for attr in dir(cursor):
        try:
            val = getattr(cursor, attr)
        except Exception:
            continue

        if not attr.startswith('_') and inspect.ismethod(val):
            try:
                print(attr, val())
            except (Exception, AssertionError) as ex:
                print(attr, 'fail', ex)
        else:
            print(attr, val)


def dumb_rename_all(c_name):
    all_known_names = dumb_rename_all.all_known_names
    simple_renames = dumb_rename_all.simple_renames

    def get_name(part):
        if part in simple_renames:
            return simple_renames[part]
        elif part.startswith('m_'):
            # apply advanced heuristics
            part = part[1:]

        return (part
                if part in all_known_names
                else inflection.underscore(part)
                )

    ret = '.'.join(get_name(part)
                   for part in c_name.split('.'))
    # print(c_name, '->', ret)
    return ret

# TODO
dumb_rename_all.all_known_names = set()
dumb_rename_all.simple_renames = {
    'nullptr': 'None',
    'true': 'True',
    'false': 'False',
}


def rename(c_name):
    return inflection.underscore(c_name)


def find_classes(cursor):
    return (list(find_kind(cursor, CursorKind.CLASS_DECL)) +
            list(find_kind(cursor, CursorKind.STRUCT_DECL)))


def find_functions(cursor):
    return list(find_kind(cursor, CursorKind.FUNCTION_DECL))


def find_methods(cursor):
    return (list(find_kind(cursor, CursorKind.CONSTRUCTOR)) +
            list(find_kind(cursor, CursorKind.DESTRUCTOR)) +
            list(find_kind(cursor, CursorKind.CXX_METHOD))
            )


def get_method_info(cursor):
    return {
        'const': cursor.is_const_method(),
        'default': cursor.is_default_method(),
        'pure_virtual': cursor.is_pure_virtual_method(),
        'static': cursor.is_static_method(),
        'virtual': cursor.is_virtual_method(),
    }


def print_ast(cursor, *, depth=0):
    print(' '.join((depth * '    ',
                    str(cursor.kind),
                    str(cursor.spelling),
                    )))
    for child in cursor.get_children():
        print_ast(child, depth=depth + 1)


def iterate(cursor):
    stack = collections.deque([cursor])
    while stack:
        cursor = stack.popleft()
        yield cursor
        stack.extend(list(cursor.get_children()))


def find_by_spelling(cursor, spelling):
    for c in iterate(cursor):
        if c.spelling == spelling:
            yield cursor


def find_kind(cursor, kind):
    for c in iterate(cursor):
        if c.kind == kind:
            yield c


class Base:
    def __init__(self, cursor, *, parent=None):
        self.parent = parent
        self.cursor = cursor
        self.parse(cursor)

    def parse(self, cursor):
        ...


class Type(Base):
    def parse(self, cursor):
        self.c_name = cursor.spelling
        self.name = self.get_python_type_name(cursor)

    def get_python_type_name(self, cursor):
        while cursor.get_pointee().spelling:
            cursor = cursor.get_pointee()
        while cursor.get_class_type().spelling:
            cursor = cursor.get_class_type()

        ret = cursor.spelling

        for strip_type in ['std::shared_ptr', 'const ']:
            ret = ret.replace(strip_type, '')

        return remove_known_namespaces(ret).strip('<>').replace('::', '.')

    def __repr__(self):
        return f'{self.name}'


class SelfArgument(Base):
    name = 'self'
    c_name = 'this'
    type = None

    def __repr__(self):
        return 'self'


class Argument(Base):
    def parse(self, cursor):
        self.type = Type(cursor.type, parent=self)
        self.c_name = cursor.spelling
        self.name = rename(self.c_name)
        if not self.name:
            self.name = inflection.underscore(
                self.type.name.split('.')[-1]
            )

    def __repr__(self):
        return f'{self.name}: {self.type}'


class Method(Base):
    allow_self_arg = True   # TODO

    def parse(self, cursor):
        self.c_name = cursor.spelling
        can_rename = self.c_name not in self.parent.python_base_attrs
        special_names = {
            'operator=': '__operator_equal__',  # incorrect
            'operator==': '__eq__',
            'operator!=': '__ne__',
            'operator>': '__gt__',
            'operator>=': '__ge__',
            'operator<': '__lt__',
            'operator<=': '__le__',
        }
        if cursor.kind == CursorKind.CONSTRUCTOR:
            self.name = '__init__'
        elif cursor.kind == CursorKind.DESTRUCTOR:
            self.name = '__del__'
        elif can_rename:
            self.name = rename(
                special_names.get(cursor.spelling, cursor.spelling)
            )
        else:
            self.name = cursor.spelling

        self.args = [Argument(c, parent=self)
                     for c in cursor.get_arguments()]

        self.saw_python_objects = set(
            arg.type for arg in self.args
            if arg.type in self.parent.python_base_namespace
        )
        self.has_retval = (cursor.result_type.spelling != 'void')
        self.result_type = Type(cursor.result_type, parent=self)
        self.comments = self.build_comments(cursor)
        if not cursor.is_static_method() and self.allow_self_arg:
            self.args.insert(0, SelfArgument(None, parent=self))

        self._source = None

    @property
    def type(self):
        return self.result_type

    @property
    def source(self):
        if self._source is None:
            parents = []

            parent = self
            while parent:
                parent = parent.parent
                if parent is not None:
                    parents.append(parent)

            self.identifier_map = {}
            for parent in reversed(parents):
                self.identifier_map.update(parent.identifier_map)

            self.identifier_map.update(**{
                attr: Identifier(attr, f'self.{attr}', None)
                for attr in self.parent.python_base_attrs
            })
            self._source = self.get_source(self.cursor)
        return self._source

    @property
    def source_body(self):
        if self._source is None:
            self.source
        return self._source_body

    def get_source(self, cursor):
        defn = cursor.get_definition()
        if defn is None:
            self._source_body = ''
            return '...'

        with open(defn.location.file.name, 'rt') as f:
            lines = f.read().splitlines()

        start, stop = defn.extent.start.line, defn.extent.end.line
        source = '\n'.join(lines[start - 1:stop])

        reference_source = textwrap.dedent(source)
        self._reference_source = reference_source
        self._source_body = self.convert_tokenized_source(defn).strip()
        if self._source_body:
            body = self._source_body
        else:
            body = '...'

        if reference_source == body:
            return '\n'.join(body)
        else:
            return '\n'.join((textwrap.indent(reference_source, '# '), body))

    def convert_tokenized_source(self, cursor):
        source = []
        identifier_map = self.identifier_map.copy()
        identifier_map.update(**{
            arg.c_name: Identifier(arg.c_name, arg.name, arg)
            for arg in self.args
        })

        identifier_map['this'] = Identifier('this', 'self', self)

        punctuation_map = {
            '++': '.increment() ',
            '--': '.decrement() ',
            ',': ', ',
            '=': ' = ',
            '::': '.',
            '->': '.',
            ';': '\n',
            '{': '\n',
            '}': '\n',
        }

        insert_at_newline = ''

        def newline():
            nonlocal insert_at_newline
            if insert_at_newline and braces > 0:
                source.append(insert_at_newline)
                insert_at_newline = ''

            source.append('\n' + ' ' * (braces * 4))

        def skip_to_cursor(cursor):
            while tokens and tokens[0].cursor.hash != cursor.hash:
                tokens.popleft()

        def keyword_handler(token, next_token, keyword):
            nonlocal insert_at_newline
            nonlocal braces
            if keyword == 'for':
                cursor = token.cursor
                # all tokens consumed by the statement:
                # token.cursor.get_tokens()
                if cursor.kind == CursorKind.CXX_FOR_RANGE_STMT:
                    var, expr, contents = list(cursor.get_children())
                    source.append('for {} in ({}):'.format(
                        self.convert_tokenized_source(var),
                        self.convert_tokenized_source(expr).strip(),
                    ))
                    newline()
                    skip_to_cursor(contents)
                elif cursor.kind == CursorKind.FOR_STMT:
                    init, check, iteration, contents = list(cursor.get_children())
                    newline()
                    source.append(self.convert_tokenized_source(init))
                    newline()
                    source.append('while ({}):  # TODO'.format(
                        self.convert_tokenized_source(check).strip(),
                    ))
                    braces += 1
                    newline()
                    source.append('{}  # TODO'.format(
                        self.convert_tokenized_source(iteration)
                    ))
                    braces -= 1
                    skip_to_cursor(contents)
            elif keyword in ('true', ):
                source.append('True')
            elif keyword in ('false', ):
                source.append('False')
            elif keyword in ('nullptr', ):
                source.append('None')
            elif keyword in ('new', 'auto'):
                source.append('')
            else:
                if keyword in ('while', 'do', 'if', 'else', 'else if'):
                    insert_at_newline = ':'
                source.append(keyword)
                if (next_token and next_token.spelling and
                        next_token.spelling[0].isalnum()):
                    source.append(' ')

        def check_identifier(identifier, context):
            first, *remainder = identifier.split('.')
            if first == 'this':
                if remainder:
                    return check_identifier('.'.join(remainder), context)
                return 'self'

            if first in context:
                if not remainder:
                    return context[first].name
                return '.'.join((context[first].name,
                                 dumb_rename_all('.'.join(remainder))))

            return dumb_rename_all(identifier)

        def lookahead_punctuation(tokens):
            punctuation = ''
            ate = []
            for token in tokens:
                spelling = token.spelling
                if token.kind == TokenKind.PUNCTUATION:
                    spelling = punctuation_map.get(spelling, spelling)
                    if spelling in '{}\n':
                        break

                    ate.append(token)
                    punctuation += spelling
                else:
                    break

            return ate, punctuation

        def lookahead_identifiers(tokens):
            identifier = ''
            ate = []
            for token in tokens:
                spelling = token.spelling
                if token.kind == TokenKind.IDENTIFIER:
                    identifier += spelling
                    ate.append(token)
                elif token.kind == TokenKind.PUNCTUATION:
                    if spelling in ('.', '->', '::'):
                        identifier += '.'
                        ate.append(token)
                    else:
                        break
                else:
                    break

            identifier = check_identifier(identifier, identifier_map)
            return ate, identifier

        def consume(token):
            nonlocal source
            nonlocal braces
            nonlocal insert_at_newline

            spelling = token.spelling
            kind = token.kind
            if kind == TokenKind.COMMENT:
                spelling = spelling.lstrip(' /')
                newline()
                source.append(f'# {spelling}')
                newline()
            elif kind == TokenKind.IDENTIFIER:
                ate, identifier = lookahead_identifiers([token, *tokens])
                for skip in ate[1:]:
                    tokens.popleft()
                if identifier in self.parent.python_base_namespace:
                    self.saw_python_objects.add(identifier)
                source.append(identifier_map.get(identifier, identifier))
            elif kind == TokenKind.KEYWORD:
                try:
                    next_token = tokens[0]
                except IndexError:
                    next_token = None
                keyword_handler(token, next_token, spelling)
            elif kind == TokenKind.PUNCTUATION:
                if spelling == '{':
                    braces += 1
                    if braces == 0:
                        source = []
                elif spelling == '}':
                    braces -= 1

                spelling = punctuation_map.get(spelling, spelling)
                if spelling == '\n':
                    newline()
                else:
                    ate, punctuation = lookahead_punctuation([token, *tokens])
                    for skip in ate[1:]:
                        tokens.popleft()
                    punctuation = re.sub('\s*\&\&\s*', ' and ', punctuation)
                    punctuation = re.sub('\s*\|\|\s*', ' or ', punctuation)
                    punctuation = re.sub('\s*==\s*', ' == ', punctuation)
                    punctuation = re.sub('\s*!=\s*', ' != ', punctuation)
                    punctuation = re.sub('\s*!\s*', ' not ', punctuation)
                    punctuation = re.sub('\s*:\s*', ' COLON ', punctuation)
                    source.append(punctuation)

                # TODO: &&, ||, << ...
            else:
                source.append(spelling)

        braces = -1  # hack to remove function header
        tokens = collections.deque(list(cursor.get_tokens()))
        while tokens:
            token = tokens.popleft()
            cursor = token.cursor
            consume(token)

        return ''.join(str(s) for s in source)

    def build_comments(self, cursor):
        if cursor.brief_comment:
            comment = cursor.brief_comment
        else:
            comment = self.name.replace('_', ' ').capitalize()

        comments = ["'''",
                    comment,
                    ]

        if self.args:
            comments.append('')
            comments.append('Parameters')
            comments.append('----------')
            for arg in self.args:
                comments.append(f'{arg.name} : {arg.type}')

        if self.has_retval:
            comments.append('')
            comments.append('Returns')
            comments.append('-------')
            comments.append(f'value : {self.result_type}')

        comments.append("'''")

        if not any((cursor.brief_comment, self.args, self.has_retval)):
            return ''
        else:
            return textwrap.indent('\n'.join(comments), ' ' * 4)

    def __repr__(self):
        arg_str = ', '.join(repr(arg) for arg in self.args)
        source = textwrap.indent(self.source, ' ' * 4)
        if self.has_retval:
            return_annotation = f' -> {self.result_type.name}'
        else:
            return_annotation = ''
        return f"""\

def {self.name}({arg_str}){return_annotation}:
{self.comments}
{source}
"""


class Function(Method):
    allow_self_arg = False   # TODO


class BaseClass(Base):
    def parse(self, cursor):
        name = cursor.spelling
        if name.startswith('class '):
            _, name = name.split(' ', 1)

        name = remove_known_namespaces(name)
        self.name = name

    def __repr__(self):
        return self.name


class Identifier:
    def __init__(self, c_name, name, obj, *, type_=None):
        self.c_name = c_name
        self.name = name
        if type_ is None and obj is not None:
            type_ = obj.type
        self.obj = obj
        self.type = type_

    def __repr__(self):
        return self.name


class Field(Base):
    def parse(self, cursor):
        self.c_name = cursor.spelling
        self.name = rename(self.c_name)
        if self.name.startswith('m_'):
            # apply advanced heuristics
            self.name = self.name[1:]

        self.type = Type(cursor.type)


class Class(BaseClass):
    skip_methods = ['tr', 'tr_utf8', 'qt_static_metacall', 'qt_metacast',
                    'qt_metacall', 'metaObject',
                    ]

    def __init__(self, cursor, *, python_base_namespace=None, parent=None):
        self.python_base_namespace = python_base_namespace
        super().__init__(cursor, parent=parent)

        self.c_name = cursor.spelling
        self.saw_python_objects = set()
        for method in self.methods:
            self.saw_python_objects |= method.saw_python_objects

        for base in self.python_bases:
            self.saw_python_objects.add(base.__name__)

    def parse(self, cursor):
        super().parse(cursor)

        self.bases = [
            BaseClass(c).name
            for c in find_kind(cursor, CursorKind.CXX_BASE_SPECIFIER)
            if c.spelling != cursor.spelling
        ]

        self.python_bases = [
            self.python_base_namespace[cls]
            for cls in self.bases
            if cls in self.python_base_namespace
        ]

        self.python_base_attrs = set(
            sum((dir(cls) for cls in self.python_bases),
                [])
        )

        self.fields = {
            c.spelling: Field(c, parent=self)
            for c in find_kind(cursor, CursorKind.FIELD_DECL)
        }

        self.identifier_map = {
            c_name: Identifier(c_name, f'self.{field.name}', field)
            for c_name, field in self.fields.items()
        }

        self.methods = []
        for method_cursor in find_methods(cursor):
            # intentionally not a list comprehension - really, we need to
            # preprocess to find these names first...
            method = Method(method_cursor, parent=self)
            self.methods.append(method)
            self.identifier_map[method.c_name] = Identifier(
                method.c_name, f'self.{method.name}', method,
                type_=method.result_type)

    @property
    def methods_to_output(self):
        for method in self.methods:
            if method.name not in self.skip_methods or method.source_body:
                yield method

    def __repr__(self):
        methods = '\n'.join(repr(m) for m in self.methods_to_output)
        if not methods:
            methods = '...'

        methods = textwrap.indent(methods, ' ' * 4)
        bases = '({})'.format(', '.join(self.bases)) if self.bases else ''
        initializer = ''
        del_method = ''
        ret = [f'class {self.name}{bases}:',
               initializer,
               del_method,
               methods]
        return '\n'.join(line for line in ret
                         if line)


class FunctionContainer:
    def __init__(self, cursor, skip_prefixes=None):
        self.parent = None
        self.cursor = cursor
        self.name = '__init__'
        self.c_name = '__init__'
        self.python_base_attrs = {}
        self.python_base_namespace = {}
        self.identifier_map = {}
        self.fields = {}
        self.saw_python_objects = {}
        self.functions = []

        if skip_prefixes is None:
            skip_prefixes = ('qt_', 'q_', 'operator')

        for cursor in find_functions(cursor):
            if not any(cursor.spelling.startswith(prefix)
                       for prefix in skip_prefixes):
                func = Function(cursor, parent=self)
                self.functions.append(func)
                self.identifier_map[func.c_name] = Identifier(
                    func.c_name, func.name, func,
                    type_=func.result_type)

        self.saw_python_objects = set()
        for func in self.functions:
            self.saw_python_objects |= func.saw_python_objects

    @property
    def methods(self):
        # compat with Class...
        return self.functions

    @property
    def functions_to_output(self):
        for func in self.functions:
            # if func.name not in self.skip_funcs or func.source_body:
            fn = func.cursor.location.file.name
            if fn and fn.startswith('/Users/klauer/docs/Repos/firstpasspiler'):
                yield func

    def __repr__(self):
        return '\n'.join(repr(func) for func in self.functions_to_output)


def remove_known_namespaces(name):
    for namespace in project_namespaces:
        if name.startswith(namespace):
            name = name[len(namespace):]
    return name


def build_namespace(modules):
    namespace = {}
    for module in modules:
        for attr in dir(module):
            cls = getattr(module, attr)
            if inspect.isclass(cls):
                namespace[attr] = cls
    return namespace


def get_all_names(items):
    all_names = set()
    for item in items:
        for attr in dir(item):
            obj = getattr(item, attr)
            all_names.add(attr)
            if inspect.isclass(obj):
                all_names = all_names.union(dir(obj))

    return all_names


def prune_classes(classes):
    # i'm sure there's a way around this, but i can't find the API
    # to get just the class definition
    clsdict = {}
    for cls in classes:
        if cls.name not in clsdict:
            clsdict[cls.name] = cls
        else:
            current = clsdict[cls.name]
            if len(cls.methods) > len(current.methods):
                clsdict[cls.name] = cls
    for name, cls in list(clsdict.items()):
        file_ = cls.cursor.location.file
        print(file_)
        if not file_:
            continue

        if file_:
            fn = file_.name
            if fn and (
                    fn.endswith('combined_source.cpp') or
                    fn.startswith('/Users/klauer/Repos/Qt-Advanced-Docking-System')
            ):
                continue
        del clsdict[name]
    return clsdict


def parse(source_path, args=None, index=None, python_base_namespace=None):
    if python_base_namespace is None:
        python_base_namespace = {}

    clang.cindex.Config.set_library_path('/usr/local/Cellar/llvm/7.0.1/lib/')

    if args is None:
        home = pathlib.Path.home()
        args = (f'-DNODE_EDITOR_EXPORTS',
                f'-DNODE_EDITOR_SHARED',
                f'-DQT_CORE_LIB',
                f'-DQT_GUI_LIB',
                f'-DQT_NO_DEBUG',
                f'-DQT_NO_KEYWORDS',
                f'-DQT_OPENGL_LIB',
                f'-DQT_WIDGETS_LIB',
                f'-Dnodes_EXPORTS',
                f'-I{home}/docs/Repos/nodeeditor/build/nodes_autogen/include',
                f'-I{home}/docs/Repos/nodeeditor/include',
                f'-I{home}/docs/Repos/nodeeditor/src',
                f'-I{home}/docs/Repos/nodeeditor/include/nodes/internal',
                f'-isystem', f'{home}/mc/envs/py36/include/qt',
                f'-isystem', f'{home}/mc/envs/py36/include/qt/QtCore',
                f'-isystem', f'{home}/mc/envs/py36/./mkspecs/macx-clang',
                f'-isystem', f'{home}/mc/envs/py36/include/qt/QtWidgets',
                f'-isystem', f'{home}/mc/envs/py36/include/qt/QtGui',
                f'-isystem', '/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX10.14.sdk/System/Library/Frameworks/OpenGL.framework/Headers',
                f'-isystem', f'{home}/mc/envs/py36/include/qt/QtOpenGL',
                f'-isysroot', f'/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX10.14.sdk',
                f'-std=c++14',
                )


    # NOTE: This is _probably_ all bad form when it comes to the clang API...
    combined_source = []

    print('Source path:', source_path)
    source_path = pathlib.Path(source_path)
    for source_file in source_path.glob('*.cpp'):
        print('Adding', source_file)
        with open(source_file, 'rt') as f:
            combined_source.append(f.read())

    if not combined_source:
        raise RuntimeError('No cpp files found')

    combined = pathlib.Path('.') / 'combined_source.cpp'
    combined = combined.absolute()

    with open(combined, 'wt') as f:
        f.write('\n'.join(combined_source))

    if index is None:
        index = clang.cindex.Index.create()

    # print(combined)
    tu = index.parse(str(combined), args=args)
    root = tu.cursor

    all_classes = []

    functions = FunctionContainer(root)
    all_classes.append(functions)

    for cursor in find_classes(root):
        # print('cls', cursor.spelling)
        # if '.cpp' in str(cursor.location):
        if not cursor.spelling.startswith('Q'):
            cls = Class(cursor, parent=functions,
                        python_base_namespace=python_base_namespace)
            functions.identifier_map[cls.c_name] = Identifier(cls.c_name,
                                                              cls.name, None)
            if cls.name and cls.name[0].isupper():
                all_classes.append(cls)

    clsdict = prune_classes(all_classes)
    return clsdict


def write_combined_output(clsdict, output_path, *, python_base_modules=None):
    if python_base_modules is None:
        python_base_modules = []
    output_path = pathlib.Path(output_path)
    with open(output_path, 'wt') as f:
        for name, cls in sorted(clsdict.items()):
            imports = set(cls.saw_python_objects)
            for module in python_base_modules:
                per_module_imports = [
                    import_ for import_ in imports
                    if import_ in dir(module)
                ]
                for import_ in per_module_imports:
                    imports.remove(import_)

                if per_module_imports:
                    per_module_imports = ', '.join(sorted(per_module_imports))
                    if per_module_imports.count(',') > 5:
                        per_module_imports = f'({per_module_imports})'

                    print(
                        f'from {module.__name__} import {per_module_imports}',
                        file=f
                    )

        for name, cls in sorted(clsdict.items()):
            output = str(cls)
            while '\n\n\n' in output:
                output = output.replace('\n\n\n', '\n\n')

            print(output, file=f)


def write_output(clsdict, output_path, *, python_base_modules=None):
    if python_base_modules is None:
        python_base_modules = []
    output_path = pathlib.Path(output_path)
    for name, cls in sorted(clsdict.items()):
        print(f'{name:30} methods: {len(cls.methods)}\t'
              f'fields {len(cls.fields)}\t'
              f'imports {len(cls.saw_python_objects)}')
        with open(output_path / f'{inflection.underscore(name)}.py',
                  'wt') as f:
            print(f'# Location: {cls.cursor.location}', file=f)
            if cls.cursor.location.file:
                print(f'# Filename: {cls.cursor.location.file.name}', file=f)
            imports = set(cls.saw_python_objects)
            for module in python_base_modules:
                per_module_imports = [
                    import_ for import_ in imports
                    if import_ in dir(module)
                ]
                for import_ in per_module_imports:
                    imports.remove(import_)

                if per_module_imports:
                    per_module_imports = ', '.join(sorted(per_module_imports))
                    if per_module_imports.count(',') > 5:
                        per_module_imports = f'({per_module_imports})'

                    print(
                        f'from {module.__name__} import {per_module_imports}',
                        file=f
                    )

            output = str(cls)
            while '\n\n\n' in output:
                output = output.replace('\n\n\n', '\n\n')

            print(output, file=f)


# TODO
project_namespaces = []
