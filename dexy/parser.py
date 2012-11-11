from dexy.plugin import PluginMeta
import copy
import dexy.doc
import dexy.exceptions
import os
import posixpath

class AbstractSyntaxTree():
    def __init__(self):
        self.lookup_table = {}
        self.tree = []

    def add_task_info(self, task_key, **kwargs):
        """
        Adds kw args to kwarg dict in lookup table dict for this task
        """
        task_key = self.standardize_key(task_key)

        if not task_key in self.tree:
            self.tree.append(task_key)

        if self.lookup_table.has_key(task_key):
            self.lookup_table[task_key].update(kwargs)
        else:
            self.lookup_table[task_key] = kwargs
            if not kwargs.has_key('children'):
                self.lookup_table[task_key]['children'] = []

        self.clean_tree()

    def add_dependency(self, task_key, child_task_key):
        """
        Adds child to list of children in lookup table dict for this task.
        """
        task_key = self.standardize_key(task_key)
        child_task_key = self.standardize_key(child_task_key)

        if task_key == child_task_key:
            return

        if not task_key in self.tree:
            self.tree.append(task_key)

        if self.lookup_table.has_key(task_key):
            self.lookup_table[task_key]['children'].append(child_task_key)
        else:
            self.lookup_table[task_key] = { 'children' : [child_task_key] }

        if not self.lookup_table.has_key(child_task_key):
            self.lookup_table[child_task_key] = { 'children' : [] }

        self.clean_tree()

    def clean_tree(self):
        """
        Removes tasks which are already represented as children.
        """
        all_children = self.all_children()

        # make copy since can't iterate and remove from same tree
        treecopy = copy.deepcopy(self.tree)

        for task in treecopy:
            if task in all_children:
                self.tree.remove(task)

    def all_children(self):
        """
        Returns a set of all task keys identified as children of some other element.
        """
        all_children = set()
        for kwargs in self.lookup_table.values():
            all_children.update(kwargs['children'])
        return all_children

    def task_kwargs(self, task_key):
        """
        Returns the dict of kw args for a task
        """
        args = self.lookup_table[task_key].copy()
        del args['children']
        return args

    def task_children(self, parent_key):
        """
        Returns the list of children for a atsk
        """
        return self.lookup_table[parent_key]['children']

    @classmethod
    def qualify_key(klass, key):
        """
        Returns key split into pattern and alias, figuring out alias if not explict.
        """
        if ":" in key:
            # split qualified key into alias & pattern
            alias, pattern = key.split(":")
        else:
            # this is an unqualified key, figure out its alias
            pattern = key

            # Allow '.ext' instead of '*.ext', shorter + easier for YAML
            if pattern.startswith("."):
                if not os.path.exists(pattern):
                    pattern = "*%s" % pattern

            if os.path.exists(pattern.split("|")[0]):
                alias = 'doc'
            elif (not "." in pattern) and (not "|" in pattern):
                alias = 'bundle'
            elif "*" in pattern:
                alias = 'pattern'
            else:
                alias = 'doc'

        alias = klass.standardize_alias(alias)
        return alias, pattern

    @classmethod
    def standardize_alias(klass, alias):
        return dexy.task.Task.aliases[alias].ALIASES[0]

    @classmethod
    def standardize_key(klass, key):
        """
        Only standardized keys should be used in the AST, so we don't create 2
        entries for what turns out to be the same task.
        """
        alias, pattern = klass.qualify_key(key)
        return "%s:%s" % (alias, pattern)

    def debug(self, log=None):
        def emit(text):
            if log:
                log.debug(text)
            else:
                print text

        emit("tree:")
        for item in self.tree:
            emit("  %s" % item)
        emit("lookup table:")
        for k, v in self.lookup_table.iteritems():
            emit("  %s: %s" % (k, v))

class Parser:
    """
    Parse various types of config file.
    """
    ALIASES = []

    __metaclass__ = PluginMeta

    @classmethod
    def is_active(klass):
        return True

    def __init__(self, wrapper=None):
        self.wrapper = wrapper

    def parse(self, input_text, directory=".", config_dirpath="."):
        ast = self.build_ast(directory, config_dirpath, input_text)
        self.wrapper.ast = ast
        ast.debug(self.wrapper.log)
        self.walk_ast(ast)

    def build_ast(self, directory, config_dirpath, input_text):
        raise Exception("Implement in subclass.")

    def adjust_task_key(self, directory, config_dirpath, task_key):
        alias, pattern = AbstractSyntaxTree.qualify_key(task_key)

        if directory == ".":
            adjusted_task_key = pattern
        else:
            adjusted_task_key = posixpath.normpath(posixpath.join(directory, pattern))

        qualified_adjusted_task_key = "%s:%s" % (alias, adjusted_task_key)

        if alias == 'pattern':
            return qualified_adjusted_task_key
        elif alias == 'doc':
            if os.path.exists(adjusted_task_key.split("|")[0]):
                return qualified_adjusted_task_key
            elif directory == config_dirpath:
                return qualified_adjusted_task_key
        elif alias == 'bundle':
            if directory == config_dirpath:
                return qualified_adjusted_task_key
        else:
            raise dexy.exceptions.InternalDexyProblem("Don't know how to add task of alias '%s'" % alias)

    def walk_ast(self, ast):
        created_tasks = {}

        def create_dexy_task(key, *child_tasks, **kwargs):
            if not key in created_tasks:
                msg = "Creating task '%s' with children '%s' with args '%s'"
                self.wrapper.log.debug(msg % (key, child_tasks, kwargs))
                alias, pattern = ast.qualify_key(key)
                task = dexy.task.Task.create(alias, pattern, *child_tasks, **kwargs)
                created_tasks[key] = task
            return created_tasks[key]

        def parse_item(key):
            children = ast.task_children(key)
            kwargs = ast.task_kwargs(key)
            kwargs['wrapper'] = self.wrapper
            if kwargs.get('inactive'):
                return

            child_tasks = [parse_item(child) for child in children if child]

            # filter out inactive children
            child_tasks = [child for child in child_tasks if child]

            return create_dexy_task(key, *child_tasks, **kwargs)

        for key in ast.tree:
            task = parse_item(key)
            self.wrapper.root_nodes.append(task)