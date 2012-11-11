from dexy.common import OrderedDict
from dexy.notify import Notify
import dexy.database
import dexy.doc
import dexy.parser
import dexy.reporter
import logging
import logging.handlers
import os
import shutil
import time

class Wrapper(object):
    """
    Class that assists in interacting with Dexy, including running Dexy.
    """

    DEFAULTS = {
            'artifacts_dir' : 'artifacts',
            'config_file' : 'dexy.conf',
            'danger' : False,
            'db_alias' : 'sqlite3',
            'db_file' : 'dexy.sqlite3',
            'disable_tests' : False,
            'dont_use_cache' : False,
            'dry_run' : False,
            'exclude' : '.git, .svn, tmp, cache, artifacts, logs, output, output-long',
            'globals' : '',
            'hashfunction' : 'md5',
            'ignore_nonzero_exit' : False,
            'log_dir' : 'logs',
            'log_file' : 'dexy.log',
            'log_format' : "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            'log_level' : "DEBUG",
            'profile' : False,
            'recurse' : True,
            'reports' : '',
            'silent' : False,
            'target' : False,
            'uselocals' : False
        }

    LOG_LEVELS = {
            'DEBUG' : logging.DEBUG,
            'INFO' : logging.INFO,
            'WARN' : logging.WARN
            }

    def __init__(self, *args, **kwargs):
        self.initialize_attribute_defaults()
        self.update_attributes_from_kwargs(kwargs)

        self.args = args
        self.root_nodes = []
        self.tasks = OrderedDict()
        self.state = None

        self.notifier = Notify(self)

    def initialize_attribute_defaults(self):
        for name, value in self.DEFAULTS.iteritems():
            setattr(self, name, value)

    def update_attributes_from_kwargs(self, kwargs):
        for key, value in kwargs.iteritems():
            if not key in self.DEFAULTS:
                raise Exception("invalid kwargs %s" % key)
            setattr(self, key, value)

    def db_path(self):
        return os.path.join(self.artifacts_dir, self.db_file)

    def log_path(self):
        return os.path.join(self.log_dir, self.log_file)

    def ete_tree(self):
        try:
            from ete2 import Tree
            t = Tree()
        except ImportError:
            return None

        t.name = "%s" % self.batch_id

        def add_children(doc, doc_node):
            for child in doc.children:
                child_node = doc_node.add_child(name=child.key_with_class())
                add_children(child, child_node)

        for doc in self.root_nodes:
            doc_node = t.add_child(name=doc.key_with_class())
            add_children(doc, doc_node)

        return t

    def run(self):
        self.batch_info = {}
        self.batch_info['start_time'] = time.time()

        self.setup_run()
        self.log.debug("batch id is %s" % self.batch_id)

        self.log.debug("Running dexy with config:")
        for k in sorted(self.__dict__):
            if not k in ('args', 'root_nodes', 'tasks', 'notifier'):
                self.log.debug("%s: %s" % (k, self.__dict__[k]))

        if self.target:
            self.log.debug("Limiting root nodes to %s" % self.target)
            docs = [doc for doc in self.root_nodes if doc.key.startswith(self.target)]
            self.log.debug("Processing nodes %s" % ", ".join(doc.key_with_class() for doc in docs))
        else:
            docs = self.root_nodes

        self.state = 'populating'

        for doc in docs:
            for task in doc:
                task()

        self.state = 'settingup'

        for doc in docs:
            for task in doc:
                task()

        self.state = 'running'

        for doc in docs:
            for task in doc:
                task()

        self.state = 'complete'

        self.save_db()
        self.setup_graph()

        self.batch_info['end_time'] = time.time()
        self.batch_info['elapsed_time'] = self.batch_info['end_time'] - self.batch_info['start_time']

    def setup_run(self):
        self.check_dexy_dirs()
        self.setup_log()
        self.setup_db()

        self.batch_id = self.db.next_batch_id()

        if not self.root_nodes:
            self.setup_docs()

    def setup_read(self, batch_id=None):
        self.check_dexy_dirs()
        self.setup_log()
        self.setup_db()

        if batch_id:
            self.batch_id = batch_id
        else:
            self.batch_id = self.db.max_batch_id()

    def check_dexy_dirs(self):
        if not (os.path.exists(self.artifacts_dir) and os.path.exists(self.log_dir)):
            raise dexy.exceptions.UserFeedback("You need to run 'dexy setup' in this directory first.")

    def setup_dexy_dirs(self):
        if not os.path.exists(self.artifacts_dir):
            os.mkdir(self.artifacts_dir)
        if not os.path.exists(self.log_dir):
            os.mkdir(self.log_dir)

    def remove_dexy_dirs(self, reports=False):
        if os.path.exists(self.artifacts_dir):
            shutil.rmtree(self.artifacts_dir)
        if os.path.exists(self.log_dir):
            shutil.rmtree(self.log_dir)

        if reports:
            if isinstance(reports, bool):
                reports=dexy.reporter.Reporter.plugins

            for report in reports:
                report.remove_reports_dir()

    def setup_log(self):
        try:
            loglevel = self.LOG_LEVELS[self.log_level.upper()]
        except KeyError:
            msg = "'%s' is not a valid log level, check python logging module docs."
            raise dexy.exceptions.UserFeedback(msg % self.log_level)

        self.log = logging.getLogger('dexy')
        self.log.setLevel(loglevel)

        handler = logging.handlers.RotatingFileHandler(
                self.log_path(),
                encoding="utf-8")

        formatter = logging.Formatter(self.log_format)
        handler.setFormatter(formatter)

        self.log.addHandler(handler)

    def setup_db(self):
        db_class = dexy.database.Database.aliases[self.db_alias]
        self.db = db_class(self)

    def setup_docs(self):
        for arg in self.args:
            self.log.debug("Processing arg %s" % arg)
            doc = self.create_doc_from_arg(arg)
            if not doc:
                raise Exception("no doc created for %s" % arg)
            doc.wrapper = self
            self.root_nodes.append(doc)

    def create_doc_from_arg(self, arg, *children, **kwargs):
        if isinstance(arg, dexy.task.Task):
            return arg

        elif isinstance(arg, list):
            if not isinstance(arg[0], basestring):
                msg = "First arg in %s should be a string" % arg
                raise dexy.exceptions.UserFeedback(msg)

            if not isinstance(arg[1], dict):
                msg = "Second arg in %s should be a dict" % arg
                raise dexy.exceptions.UserFeedback(msg)

            if kwargs:
                raise Exception("Shouldn't have kwargs if arg is a list")

            if children:
                raise Exception("Shouldn't have children if arg is a list")

            alias, pattern = dexy.parser.AbstractSyntaxTree.qualify_key(arg[0])
            return dexy.task.Task.create(alias, pattern, **arg[1])

        elif isinstance(arg, basestring):
            alias, pattern = dexy.parser.AbstractSyntaxTree.qualify_key(arg[0])
            return dexy.task.Task.create(alias, pattern, *children, **kwargs)

        else:
            raise Exception("unknown arg type %s for arg %s" % (arg.__class__.__name__, arg))

    def save_db(self):
        self.db.save()

    def run_docs(self, *docs):
        """
        Convenience method for testing to add docs and then run them.
        """
        self.setup_dexy_dirs()
        self.root_nodes = docs
        self.run()

    def register(self, task):
        """
        Register a task with the wrapper
        """
        self.tasks[task.key_with_class()] = task
        self.notifier.subscribe("newchild", task.handle_newchild)

    def registered_docs(self):
        return [d for d in self.tasks.values() if isinstance(d, dexy.doc.Doc)]

    def registered_doc_names(self):
        return [d.name for d in self.registered_docs()]

    def reports_dirs(self):
        return [c.REPORTS_DIR for c in dexy.reporter.Reporter.plugins]

    def report(self, *reporters):
        """
        Runs reporters. Either runs reporters which have been passed in or, if
        none, then runs all available reporters which have ALLREPORTS set to
        true.
        """
        if not reporters:
            reporters = [c() for c in dexy.reporter.Reporter.plugins if c.ALLREPORTS]

        for reporter in reporters:
            self.log.debug("Running reporter %s" % reporter.ALIASES[0])
            reporter.run(self)

    def get_child_hashes_in_previous_batch(self, parent_hashstring):
        return self.db.get_child_hashes_in_previous_batch(self.batch_id, parent_hashstring)

    def config_for_directory(self, path):
        path_elements = path.split(os.sep)

        config = OrderedDict()

        for i in range(1,len(path_elements)+1):
            parent_dir_path = os.path.join(*(path_elements[0:i]))
            config[parent_dir_path] = {}

            for k in dexy.parser.Parser.aliases.keys():
                config_file_in_directory = os.path.join(parent_dir_path, k)
                if os.path.exists(config_file_in_directory):
                    self.log.debug("found doc config file '%s'" % config_file_in_directory)
                    with open(config_file_in_directory, "r") as f:
                        config[parent_dir_path][k] = f.read()

        return config

    def load_doc_config(self):
        """
        Look for document config files in current working tree and load them.
        """
        exclude = self.exclude_dirs()

        for dirpath, dirnames, filenames in os.walk("."):
            for x in exclude:
                if x in dirnames:
                    dirnames.remove(x)

            nodexy_file = os.path.join(dirpath, '.nodexy')
            if os.path.exists(nodexy_file):
                # ...remove all child dirs from processing...
                for i in xrange(len(dirnames)):
                    dirnames.pop()
            else:
                # this dir is ok
                config_for_dir = self.config_for_directory(dirpath)
                for config_dirname, config_dict in config_for_dir.iteritems():
                    for alias, config_text in config_dict.iteritems():
                        parser = dexy.parser.Parser.aliases[alias](self)
                        parser.parse(config_text, dirpath, config_dirname)

    def setup_config(self):
        self.setup_dexy_dirs()
        self.setup_log()
        self.load_doc_config()

    def cleanup_partial_run(self):
        if hasattr(self, 'db'):
            # TODO remove any entries which don't have
            self.db.save()

    def setup_graph(self):
        """
        Creates a dot representation of the tree.
        """
#        graph = self.ete_tree()
        graph = ["digraph G {"]

        for task in self.tasks.values():
            if hasattr(task, 'artifacts'):
                task_label = task.key_with_class().replace("|", "\|")
                label = """   "%s" [shape=record, label="%s\\n\\n""" % (task.key_with_class(), task_label)
                for child in task.artifacts:
                    label += "%s\l" % child.key_with_class().replace("|", "\|")

                label += "\"];"
                graph.append(label)

                for child in task.children:
                    if not child in task.artifacts:
                        graph.append("""   "%s" -> "%s";""" % (task.key_with_class(), child.key_with_class()))

            elif "Artifact" in task.__class__.__name__:
                pass
            else:
                graph.append("""   "%s" [shape=record];""" % task.key_with_class())
                for child in task.children:
                    graph.append("""   "%s" -> "%s";""" % (task.key_with_class(), child.key_with_class()))


        graph.append("}")

        self.graph = "\n".join(graph)

    def exclude_dirs(self):
        return [d.strip() for d in self.exclude.split(",")]

    def walk(self, start):
        exclude = self.exclude_dirs()

        for dirpath, dirnames, filenames in os.walk(start):
            for x in exclude:
                if x in dirnames:
                    dirnames.remove(x)

            nodexy_file = os.path.join(dirpath, '.nodexy')
            if not os.path.exists(nodexy_file):
                for filename in filenames:
                    yield(dirpath, filename)