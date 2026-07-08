'''
Implements access to the NVMe target configfs hierarchy

Copyright (c) 2011-2013 by Datera, Inc.
Copyright (c) 2011-2014 by Red Hat, Inc.
Copyright (c) 2016 by HGST, a Western Digital Company.

Licensed under the Apache License, Version 2.0 (the "License"); you may
not use this file except in compliance with the License. You may obtain
a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
License for the specific language governing permissions and limitations
under the License.
'''

import os
import stat
import uuid
import json
import subprocess
import shlex
from glob import iglob as glob

DEFAULT_SAVE_FILE = '/etc/nvmet/config.json'


class CFSError(Exception):
    '''
    Generic slib error.
    '''
    pass


class CFSNotFound(CFSError):
    '''
    The underlying configfs object does not exist. Happens when
    calling methods of an object that is instantiated but have
    been deleted from configfs, or when trying to lookup an
    object that does not exist.
    '''
    pass


class CFSNode:
    '''
    A node in the configfs filesystem.
    This is the base class for all other objects.
    '''

    configfs_dir = '/sys/kernel/config/nvmet'

    def __init__(self):
        self._path = self.configfs_dir
        self._enable = None
        self.attr_groups = []

    def __eq__(self, other):
        '''
        Checks if two CFSNode objects are equal.
        '''
        return self._path == other._path

    def __ne__(self, other):
        '''
        Checks if two CFSNode objects are not equal.
        '''
        return self._path != other._path

    def _get_path(self):
        '''
        Returns the path of the CFSNode.
        '''
        return self._path

    def _create_in_cfs(self, mode):
        '''
        Creates the configFS node if it does not already exist, depending on
        the mode.
        any -> makes sure it exists, also works if the node already does exist
        lookup -> make sure it does NOT exist
        create -> create the node which must not exist beforehand
        '''
        if mode not in ['any', 'lookup', 'create']:
            raise CFSError(f"Invalid mode: {mode}")
        if self.exists and mode == 'create':
            raise CFSError(f"This {self.__class__.__name__} already "
                           f"exists in configFS")
        if not self.exists and mode == 'lookup':
            raise CFSNotFound(f"No such {self.__class__.__name__} in "
                              f"configfs: {self.path}")

        if not self.exists:
            try:
                os.mkdir(self.path)
            except Exception as exc:
                raise CFSError(f"Could not create {self.__class__.__name__}"
                               f" in configFS") from exc
        self.get_enable()

    def _exists(self):
        '''
        Returns True if the CFSNode exists, False otherwise.
        '''
        return os.path.isdir(self.path)

    def _check_self(self):
        '''
        Checks if the CFSNode exists.
        '''
        if not self.exists:
            raise CFSNotFound(f"This {self.__class__.__name__} does not "
                              f"exist in configFS")

    def list_attrs(self, group, writable=None):
        '''
        @param group: The attribute group
        @param writable: If None (default), returns all attributes, if True,
        returns read-write attributes, if False, returns just the read-only
        attributes.
        @type writable: bool or None
        @return: A list of existing attribute names as strings.
        '''
        self._check_self()

        names = [os.path.basename(name).split('_', 1)[1]
                 for name in glob(f"{self._path}/{group}_*")
                 if os.path.isfile(name)]

        if writable is True:
            names = [name for name in names
                     if self._attr_is_writable(group, name)]
        elif writable is False:
            names = [name for name in names
                     if not self._attr_is_writable(group, name)]

        names.sort()
        return names

    def _attr_is_writable(self, group, name):
        '''
        Returns True if the attribute is writable, False otherwise.
        '''
        s = os.stat(f"{self._path}/{group}_{name}")
        return s[stat.ST_MODE] & stat.S_IWUSR

    def set_attr(self, group, attribute, value):
        '''
        Sets the value of a named attribute.
        The attribute must exist in configFS.
        @param group: The attribute group
        @param attribute: The attribute's name.
        @param value: The attribute's value.
        @type value: string
        '''
        self._check_self()
        path = f"{self.path}/{str(group)}_{str(attribute)}"

        if not os.path.isfile(path):
            raise CFSError(f"Cannot find attribute: {path}")

        if self._enable:
            raise CFSError(f"Cannot set attribute while "
                           f"{self.__class__.__name__} is enabled")

        try:
            with open(path, 'w', encoding="utf-8") as file_fd:
                file_fd.write(str(value))
        except Exception as e:
            raise CFSError(f"Cannot set attribute {path}: {e}") from e

    def get_attr(self, group, attribute):
        '''
        Gets the value of a named attribute.
        @param group: The attribute group
        @param attribute: The attribute's name.
        @return: The named attribute's value, as a string.
        '''
        self._check_self()
        path = f"{self.path}/{str(group)}_{str(attribute)}"
        if not os.path.isfile(path):
            raise CFSError(f"Cannot find attribute: {path}")

        with open(path, 'r', encoding="utf-8") as file_fd:
            return file_fd.read().strip()

    def get_enable(self):
        '''
        Returns the value of the 'enable' attribute.
        '''
        self._check_self()
        path = f"{self.path}/enable"
        if not os.path.isfile(path):
            return None

        with open(path, 'r', encoding="utf-8") as file_fd:
            self._enable = int(file_fd.read().strip())
        return self._enable

    def set_enable(self, value):
        '''
        Sets the value of the 'enable' attribute.
        '''
        self._check_self()
        path = f"{self.path}/enable"

        if not os.path.isfile(path) or self._enable is None:
            raise CFSError(f"Cannot enable {self.path}")

        try:
            with open(path, 'w', encoding="utf-8") as file_fd:
                file_fd.write(str(value))
        except Exception as e:
            raise CFSError(f"Cannot enable {self.path}: {e} ({value})") from e
        self._enable = value

    def delete(self):
        '''
        If the underlying configFS object does not exist, this method does
        nothing. If the underlying configFS object exists, this method attempts
        to delete it.
        '''
        if self.exists:
            os.rmdir(self.path)

    path = property(_get_path,
                    doc="Get the configFS object path.")
    exists = property(_exists,
                      doc="Is True as long as the underlying configFS object"
                      + " exists. If the underlying configFS objects gets"
                      + " deleted either by calling the delete() method, or by"
                      + " any other means, it will be False.")

    def dump(self):
        '''
        Returns a dict with the config of the object.
        '''
        d = {}
        for group in self.attr_groups:
            a = {}
            for i in self.list_attrs(group, writable=True):
                a[str(i)] = self.get_attr(group, i)
            d[str(group)] = a
        if self._enable is not None:
            d['enable'] = self._enable
        return d

    def _setup_attrs(self, attr_dict, err_func):
        '''
        Set up attributes from a dict.
        '''
        for group in self.attr_groups:
            for name, value in attr_dict.get(group, {}).items():
                try:
                    self.set_attr(group, name, value)
                except CFSError as e:
                    err_func(str(e))
        enable = attr_dict.get('enable')
        if enable is not None:
            self.set_enable(enable)


class Root(CFSNode):
    '''
    The root of the NVMe target configfs hierarchy.
    '''
    def __init__(self):
        super().__init__()

        self.attr_groups = ['discovery']
        if not os.path.isdir(self.configfs_dir):
            self._modprobe('nvmet')

        if not os.path.isdir(self.configfs_dir):
            raise CFSError(f"{self.configfs_dir} does not exist.  Giving up.")

        self._path = self.configfs_dir
        self._create_in_cfs('lookup')

    def _modprobe(self, modname):
        try:
            from kmodpy import kmod

            try:
                kmod.Kmod().modprobe(modname, quiet=True)
            except kmod.KmodError:
                pass
        except ImportError:
            # Try the ctypes library included with the libkmod itself.
            try:
                import kmod

                try:
                    kmod.Kmod().modprobe(modname)
                except Exception:
                    pass
            except ImportError:
                # Try the binary specified in /proc
                try:
                    modprobe_cmd = None
                    with open('/proc/sys/kernel/modprobe', 'r') as f:
                        modprobe_cmd = f.read()
                    if modprobe_cmd:
                        subprocess.run(shlex.split(modprobe_cmd) + [modname],
                                       check=False)
                except Exception:
                    pass

    def _list_subsystems(self):
        self._check_self()

        for d in os.listdir(f"{self._path}/subsystems/"):
            yield Subsystem(d, 'lookup')

    subsystems = property(_list_subsystems,
                          doc="Get the list of Subsystems.")

    def _list_ports(self):
        self._check_self()

        for d in os.listdir(f"{self._path}/ports/"):
            yield Port(d, 'lookup')

    ports = property(_list_ports,
                     doc="Get the list of Ports.")

    def _list_hosts(self):
        self._check_self()

        for h in os.listdir(f"{self._path}/hosts/"):
            yield Host(h, 'lookup')

    hosts = property(_list_hosts,
                     doc="Get the list of Hosts.")

    def save_to_file(self, savefile=None):
        '''
        Write the configuration in json format to a file.
        '''
        if savefile:
            savefile = os.path.expanduser(savefile)
        else:
            savefile = DEFAULT_SAVE_FILE

        savefile_abspath = os.path.abspath(savefile)
        savefile_dir = os.path.dirname(savefile_abspath)
        if not os.path.exists(savefile_dir):
            os.makedirs(savefile_dir)

        with open(savefile + ".temp", "w+", encoding="utf-8") as f:
            os.fchmod(f.fileno(), stat.S_IRUSR | stat.S_IWUSR)
            f.write(json.dumps(self.dump(), sort_keys=True, indent=2))
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        os.rename(savefile + ".temp", savefile)

        # Sync the containing directory too
        dir_fd = None
        try:
            dir_fd = os.open(savefile_dir, os.O_RDONLY)
            os.fsync(dir_fd)
        finally:
            if dir_fd:
                os.close(dir_fd)

    def clear_existing(self):
        '''
        Remove entire current configuration.
        '''

        for p in self.ports:
            p.delete()
        for s in self.subsystems:
            s.delete()
        for h in self.hosts:
            h.delete()

    def restore(self, config, clear_existing=False, abort_on_error=False):
        '''
        Takes a dict generated by dump() and reconfigures the target to match.
        Returns list of non-fatal errors that were encountered.
        Will refuse to restore over an existing configuration unless
        clear_existing is True.
        '''
        if clear_existing:
            self.clear_existing()
        else:
            if any(self.subsystems):
                raise CFSError("subsystems present, not restoring")

        errors = []

        if abort_on_error:
            def err_func(err_str):
                raise CFSError(err_str)
        else:
            def err_func(err_str):
                errors.append(err_str + ", skipped")

        # Create the hosts first because the subsystems reference them
        for index, t in enumerate(config.get('hosts', [])):
            if 'nqn' not in t:
                err_func(f"'nqn' not defined in host {index}")
                continue

            Host.setup(t, err_func)

        for index, t in enumerate(config.get('subsystems', [])):
            if 'nqn' not in t:
                err_func(f"'nqn' not defined in subsystem {index}")
                continue

            Subsystem.setup(t, err_func)

        for index, t in enumerate(config.get('ports', [])):
            if 'portid' not in t:
                err_func(f"'portid' not defined in port {index}")
                continue

            Port.setup(self, t, err_func)

        return errors

    def restore_from_file(self, savefile=None, clear_existing=True,
                          abort_on_error=False):
        '''
        Restore the configuration from a file in json format.
        Returns a list of non-fatal errors. If abort_on_error is set,
          it will raise the exception instead of continuing.
        '''
        if savefile:
            savefile = os.path.expanduser(savefile)
        else:
            savefile = DEFAULT_SAVE_FILE

        with open(savefile, "r", encoding="utf-8") as f:
            config = json.loads(f.read())
            return self.restore(config, clear_existing=clear_existing,
                                abort_on_error=abort_on_error)

    def dump(self):
        d = super().dump()
        d['subsystems'] = [s.dump() for s in self.subsystems]
        d['ports'] = [p.dump() for p in self.ports]
        d['hosts'] = [h.dump() for h in self.hosts]
        return d


class Subsystem(CFSNode):
    '''
    This is an interface to a NVMe Subsystem in configFS.
    A Subsystem is identified by its NQN.
    '''

    def __repr__(self):
        return f"<Subsystem {self.nqn}>"

    def __init__(self, nqn=None, mode='any'):
        '''
        @param nqn: The Subsystems' NQN.
            If no NQN is specified, one will be generated.
        @type nqn: string
        @param mode:An optional string containing the object creation mode:
            - I{'any'} means the configFS object will be either looked up
              or created.
            - I{'lookup'} means the object MUST already exist configFS.
            - I{'create'} means the object must NOT already exist in configFS.
        @type mode:string
        @return: A Subsystem object.
        '''
        super().__init__()

        if nqn is None:
            if mode == 'lookup':
                raise CFSError("Need NQN for lookup")
            nqn = self._generate_nqn()

        self.nqn = nqn
        self.attr_groups = ['attr']
        self._path = f"{self.configfs_dir}/subsystems/{nqn}"
        self._create_in_cfs(mode)

    def _generate_nqn(self):
        '''
        Generates a new NQN.
        '''
        prefix = "nqn.2014-08.org.nvmexpress:NVMf:uuid"
        name = str(uuid.uuid4())
        return f"{prefix}:{name}"

    def delete(self):
        '''
        Recursively deletes a Subsystem object.
        This will delete all attached Namespace objects and then the
        Subsystem itself.
        '''
        self._check_self()
        for ns in self.namespaces:
            ns.delete()
        for h in self.allowed_hosts:
            self.remove_allowed_host(h)
        super().delete()

    def _list_namespaces(self):
        '''
        Lists the namespaces of the subsystem.
        '''
        self._check_self()
        for d in os.listdir(f"{self._path}/namespaces/"):
            yield Namespace(self, int(d), 'lookup')

    namespaces = property(_list_namespaces,
                          doc="Get the list of Namespaces for the Subsystem.")

    def _get_passthru(self):
        '''
        Returns the passthru object of the subsystem.
        '''
        self._check_self()
        return Passthru(self)

    passthru = property(_get_passthru,
                        doc="Get the passthru node for the subsystem")

    def _list_allowed_hosts(self):
        '''
        Lists the allowed hosts of the subsystem.
        '''
        return [os.path.basename(name)
                for name in os.listdir(f"{self._path}/allowed_hosts/")]

    allowed_hosts = property(_list_allowed_hosts,
                             doc="Get the list of Allowed Hosts for the "
                             + "Subsystem.")

    def add_allowed_host(self, nqn):
        '''
        Enable access for the host identified by I{nqn} to the Subsystem
        '''
        try:
            os.symlink(f"{self.configfs_dir}/hosts/{nqn}",
                       f"{self._path}/allowed_hosts/{nqn}")
        except Exception as e:
            raise CFSError(f"Could not symlink {nqn} in configFS: {e}") from e

    def remove_allowed_host(self, nqn):
        '''
        Disable access for the host identified by I{nqn} to the Subsystem
        '''
        try:
            os.unlink(f"{self._path}/allowed_hosts/{nqn}")
        except Exception as e:
            raise CFSError(f"Could not unlink {nqn} in configFS: {e}") from e

    def has_passthru(self):
        '''
        Check if the subsystem has a passthru node.
        '''
        return os.path.isdir(os.path.join(self.path, "passthru"))

    @classmethod
    def setup(cls, t, err_func):
        '''
        Set up Subsystem objects based upon t dict, from saved config.
        Guard against missing or bad dict items, but keep going.
        Call 'err_func' for each error.
        '''

        if 'nqn' not in t:
            err_func("'nqn' not defined for Subsystem")
            return

        try:
            s = Subsystem(t['nqn'])
        except CFSError as e:
            err_func(f"Could not create Subsystem object: {e}")
            return

        for ns in t.get('namespaces', []):
            Namespace.setup(s, ns, err_func)
        for h in t.get('allowed_hosts', []):
            s.add_allowed_host(h)
        for pt in t.get('passthru', []):
            Passthru.setup(s, pt, err_func)

        s._setup_attrs(t, err_func)

    def dump(self):
        d = super().dump()
        d['nqn'] = self.nqn
        d['namespaces'] = [ns.dump() for ns in self.namespaces]
        d['allowed_hosts'] = self.allowed_hosts
        if self.has_passthru():
            d['passthru'] = [self.passthru.dump()]
        return d


class Namespace(CFSNode):
    '''
    This is an interface to a NVMe Namespace in configFS.
    A Namespace is identified by its parent Subsystem and Namespace ID.
    '''

    MAX_NSID = 8192

    def __repr__(self):
        return f"<Namespace {self.nsid}>"

    def __init__(self, subsystem, nsid=None, mode='any'):
        '''
        @param subsystem: The parent Subsystem object
        @param nsid: The Namespace identifier
            If no nsid is specified, the next free one will be used.
        @type nsid: int
        @param mode:An optional string containing the object creation mode:
            - I{'any'} means the configFS object will be either looked up
              or created.
            - I{'lookup'} means the object MUST already exist configFS.
            - I{'create'} means the object must NOT already exist in configFS.
        @type mode:string
        @return: A Namespace object.
        '''
        super().__init__()

        if not isinstance(subsystem, Subsystem):
            raise CFSError("Invalid parent class")

        if nsid is None:
            if mode == 'lookup':
                raise CFSError("Need NSID for lookup")

            nsids = [n.nsid for n in subsystem.namespaces]
            for index in range(1, self.MAX_NSID + 1):
                if index not in nsids:
                    nsid = index
                    break
            if nsid is None:
                raise CFSError(f"All NSIDs 1-{self.MAX_NSID} in use")
        else:
            nsid = int(nsid)
            if nsid < 1 or nsid > self.MAX_NSID:
                raise CFSError(f"NSID must be 1 to {self.MAX_NSID}")

        self.attr_groups = ['device', 'ana', 'resv']
        self._subsystem = subsystem
        self._nsid = nsid
        self._path = f"{self.subsystem.path}/namespaces/{self.nsid}"
        self._create_in_cfs(mode)

    def _get_subsystem(self):
        '''
        Returns the parent subsystem.
        '''
        return self._subsystem

    def _get_nsid(self):
        '''
        Returns the namespace ID.
        '''
        return self._nsid

    def _get_grpid(self):
        '''
        Returns the ANA group ID.
        '''
        self._check_self()
        _grpid = 0
        path = f"{self.path}/ana_grpid"
        if os.path.isfile(path):
            with open(path, 'r', encoding="utf-8") as file_fd:
                _grpid = int(file_fd.read().strip())
        return _grpid

    def set_grpid(self, grpid):
        '''
        Sets the ANA group ID.
        '''
        self._check_self()
        path = f"{self.path}/ana_grpid"
        if os.path.isfile(path):
            with open(path, 'w', encoding="utf-8") as file_fd:
                file_fd.write(str(grpid))

    grpid = property(_get_grpid, doc="Get the ANA Group ID.")

    subsystem = property(_get_subsystem,
                         doc="Get the parent Subsystem object.")
    nsid = property(_get_nsid, doc="Get the NSID as an int.")

    @classmethod
    def setup(cls, subsys, n, err_func):
        '''
        Set up a Namespace object based upon n dict, from saved config.
        Guard against missing or bad dict items, but keep going.
        Call 'err_func' for each error.
        '''

        if 'nsid' not in n:
            err_func("'nsid' not defined for Namespace")
            return

        try:
            ns = Namespace(subsys, n['nsid'])
        except CFSError as e:
            err_func(f"Could not create Namespace object: {e}")
            return

        ns._setup_attrs(n, err_func)
        if 'ana_grpid' in n:
            ns.set_grpid(int(n['ana_grpid']))

    def dump(self):
        '''
        Returns a dict with the config of the object.
        '''
        d = super().dump()
        d['nsid'] = self.nsid
        d['ana_grpid'] = self.grpid
        return d


class Passthru(CFSNode):
    '''
    This is an interface to a NVMe passthru in ConfigFS.
    A Passthru is identified by its parent Subsystem.
    '''

    def __init__(self, subsystem):
        '''
        @param subsystem: The parent Subsystem object.
        @return: A Passthru object.
        '''
        super().__init__()
        self._path = f"{subsystem.path}/passthru"
        self.attr_groups = ['device']

    def _get_clear_ids(self):
        '''
        Get the passthru namespace clear_ids attribute.
        '''
        self._check_self()
        path = f"{self.path}/clear_ids"
        _ids = 0
        if os.path.isfile(path):
            with open(path, 'r', encoding="utf-8") as file_fd:
                _ids = int(file_fd.read().strip())
        return _ids

    ids = property(_get_clear_ids,
                   doc="Get the passthru namespace clear_ids attribute.")

    def set_clear_ids(self, clear):
        '''
        Set the passthru namespace clear_ids attribute.
        '''
        self._check_self()
        path = f"{self.path}/clear_ids"
        if os.path.isfile(path):
            with open(path, 'w', encoding="utf-8") as file_fd:
                file_fd.write(str(clear))

    def _get_admin_timeout(self):
        '''
        Get the passthru admin command timeout.
        '''
        self._check_self()
        path = f"{self.path}/admin_timeout"
        _timeout = 0
        if os.path.isfile(path):
            with open(path, 'r', encoding="utf-8") as file_fd:
                _timeout = int(file_fd.read().strip())
        return _timeout

    admin_timeout = property(_get_admin_timeout,
                             doc="Get the passthru admin command timeout.")

    def set_admin_timeout(self, timeout):
        '''
        Set the passthru admin command timeout.
        '''
        self._check_self()
        path = f"{self.path}/admin_timeout"
        if os.path.isfile(path):
            with open(path, 'w', encoding="utf-8") as file_fd:
                file_fd.write(str(timeout))

    def _get_io_timeout(self):
        '''
        Get the passthru IO command timeout.
        '''
        self._check_self()
        path = f"{self.path}/io_timeout"
        _timeout = 0
        if os.path.isfile(path):
            with open(path, 'r', encoding="utf-8") as file_fd:
                _timeout = int(file_fd.read().strip())
        return _timeout

    io_timeout = property(_get_io_timeout,
                          doc="Get the passthru IO command timeout.")

    def set_io_timeout(self, timeout):
        '''
        Set the passthru IO command timeout.
        '''
        self._check_self()
        path = f"{self.path}/io_timeout"
        if os.path.isfile(path):
            with open(path, 'w', encoding="utf-8") as file_fd:
                file_fd.write(str(timeout))

    @classmethod
    def setup(cls, subsys, p, err_func):
        '''
        Set up a Passthru object based upon p dict, from saved config.
        '''
        try:
            pt = Passthru(subsys)
        except CFSError as e:
            err_func(f"Could not create Passthru object: {e}")
            return
        pt._setup_attrs(p, err_func)
        if 'clear_ids' in p:
            pt.set_clear_ids(int(p['clear_ids']))
        if 'admin_timeout' in p:
            pt.set_admin_timeout(int(p['admin_timeout']))
        if 'io_timeout' in p:
            pt.set_io_timeout(int(p['io_timeout']))

    def dump(self):
        '''
        Returns a dict with the config of the object.
        '''
        d = super().dump()
        d['clear_ids'] = self.ids
        d['admin_timeout'] = self.admin_timeout
        d['io_timeout'] = self.io_timeout
        return d


class Port(CFSNode):
    '''
    This is an interface to a NVMe Port in configFS.
    '''

    MAX_PORTID = 8192

    def __repr__(self):
        return f"<Port {self.portid}>"

    def __init__(self, portid, mode='any'):
        super().__init__()

        self.attr_groups = ['addr', 'param']
        self._portid = int(portid)
        self._path = f"{self.configfs_dir}/ports/{self._portid}"
        self._create_in_cfs(mode)

    def _get_portid(self):
        '''
        Returns the port ID.
        '''
        return self._portid

    portid = property(_get_portid, doc="Get the Port ID as an int.")

    def _list_subsystems(self):
        '''
        Lists the subsystems of the port.
        '''
        return [os.path.basename(name)
                for name in os.listdir(f"{self._path}/subsystems/")]

    subsystems = property(_list_subsystems,
                          doc="Get the list of Subsystem for this Port.")

    def add_subsystem(self, nqn):
        '''
        Enable access to the Subsystem identified by I{nqn} through this Port.
        '''
        try:
            os.symlink(f"{self.configfs_dir}/subsystems/{nqn}",
                       f"{self._path}/subsystems/{nqn}")
        except Exception as e:
            raise CFSError(f"Could not symlink {nqn} in configFS: {e}") from e

    def remove_subsystem(self, nqn):
        '''
        Disable access to the Subsystem identified by I{nqn} through this Port.
        '''
        try:
            os.unlink(f"{self._path}/subsystems/{nqn}")
        except Exception as e:
            raise CFSError(f"Could not unlink {nqn} in configFS: {e}") from e

    def delete(self):
        '''
        Recursively deletes a Port object.
        '''
        self._check_self()
        for s in self.subsystems:
            self.remove_subsystem(s)
        for a in self.ana_groups:
            a.delete()
        for r in self.referrals:
            r.delete()
        super().delete()

    def _list_referrals(self):
        '''
        Lists the referrals of the port.
        '''
        self._check_self()
        for d in os.listdir(f"{self._path}/referrals/"):
            yield Referral(self, d, 'lookup')

    referrals = property(_list_referrals,
                         doc="Get the list of Referrals for this Port.")

    def _list_ana_groups(self):
        '''
        Lists the ANA groups of the port.
        '''
        self._check_self()
        if os.path.isdir(f"{self._path}/ana_groups/"):
            for d in os.listdir(f"{self._path}/ana_groups/"):
                yield ANAGroup(self, int(d), 'lookup')

    ana_groups = property(_list_ana_groups,
                          doc="Get the list of ANA Groups for this Port.")

    @classmethod
    def setup(cls, root, n, err_func):
        '''
        Set up a Port object based upon n dict, from saved config.
        Guard against missing or bad dict items, but keep going.
        Call 'err_func' for each error.
        '''

        if 'portid' not in n:
            err_func("'portid' not defined for Port")
            return

        try:
            port = Port(n['portid'])
        except CFSError as e:
            err_func(f"Could not create Port object: {e}")
            return

        port._setup_attrs(n, err_func)
        for s in n.get('subsystems', []):
            port.add_subsystem(s)
        for a in n.get('ana_groups', []):
            ANAGroup.setup(port, a, err_func)
        for r in n.get('referrals', []):
            Referral.setup(port, r, err_func)

    def dump(self):
        '''
        Returns a dict with the config of the object.
        '''
        d = super().dump()
        d['portid'] = self.portid
        d['subsystems'] = self.subsystems
        d['ana_groups'] = [a.dump() for a in self.ana_groups]
        d['referrals'] = [r.dump() for r in self.referrals]
        return d


class Referral(CFSNode):
    '''
    This is an interface to a NVMe Referral in configFS.
    '''

    def __repr__(self):
        return f"<Referral {self.name}>"

    def __init__(self, port, name, mode='any'):
        super().__init__()

        if not isinstance(port, Port):
            raise CFSError("Invalid parent class")

        self.attr_groups = ['addr']
        self.port = port
        self._name = name
        self._path = f"{self.port.path}/referrals/{self._name}"
        self._create_in_cfs(mode)

    def _get_name(self):
        '''
        Returns the name of the referral.
        '''
        return self._name

    name = property(_get_name, doc="Get the Referral name.")

    @classmethod
    def setup(cls, port, n, err_func):
        '''
        Set up a Referral based upon n dict, from saved config.
        Guard against missing or bad dict items, but keep going.
        Call 'err_func' for each error.
        '''

        if 'name' not in n:
            err_func("'name' not defined for Referral")
            return

        try:
            r = Referral(port, n['name'])
        except CFSError as e:
            err_func(f"Could not create Referral object: {e}")
            return

        r._setup_attrs(n, err_func)

    def dump(self):
        '''
        Returns a dict with the config of the object.
        '''
        d = super().dump()
        d['name'] = self.name
        return d


class ANAGroup(CFSNode):
    '''
    This is an interface to a NVMe ANA Group in configFS.
    '''

    MAX_GRPID = 1024

    def __repr__(self):
        return f"<ANA Group {self.grpid}>"

    def __init__(self, port, grpid, mode='any'):
        super().__init__()

        if not os.path.isdir(f"{port.path}/ana_groups"):
            raise CFSError("ANA not supported")

        if grpid is None:
            if mode == 'lookup':
                raise CFSError("Need grpid for lookup")

            grpids = [n.grpid for n in port.ana_groups]
            for index in range(2, self.MAX_GRPID + 1):
                if index not in grpids:
                    grpid = index
                    break
            if grpid is None:
                raise CFSError(f"All ANA Group IDs 1-{self.MAX_GRPID} in use")
        else:
            grpid = int(grpid)
            if grpid < 1 or grpid > self.MAX_GRPID:
                raise CFSError(f"GRPID {grpid} must be 1 to {self.MAX_GRPID}")

        self.attr_groups = ['ana']
        self._port = port
        self._grpid = grpid
        self._path = f"{self._port.path}/ana_groups/{self.grpid}"
        self._create_in_cfs(mode)

    def _get_grpid(self):
        '''
        Returns the ANA group ID.
        '''
        return self._grpid

    grpid = property(_get_grpid, doc="Get the ANA Group ID.")

    @classmethod
    def setup(cls, port, n, err_func):
        '''
        Set up an ANA Group object based upon n dict, from saved config.
        Guard against missing or bad dict items, but keep going.
        Call 'err_func' for each error.
        '''

        if 'grpid' not in n:
            err_func("'grpid' not defined for ANA Group")
            return

        try:
            a = ANAGroup(port, n['grpid'])
        except CFSError as e:
            err_func(f"Could not create ANA Group object: {e}")
            return

        a._setup_attrs(n, err_func)

    def delete(self):
        '''
        Deletes the ANA group.
        '''
        # ANA Group 1 is automatically created/deleted
        if self.grpid != 1:
            super().delete()

    def dump(self):
        '''
        Returns a dict with the config of the object.
        '''
        d = super().dump()
        d['grpid'] = self.grpid
        return d


class Host(CFSNode):
    '''
    This is an interface to a NVMe Host in configFS.
    A Host is identified by its NQN.
    '''

    def __repr__(self):
        return f"<Host {self.nqn}>"

    def __init__(self, nqn, mode='any'):
        '''
        @param nqn: The Hosts's NQN.
        @type nqn: string
        @param mode:An optional string containing the object creation mode:
            - I{'any'} means the configFS object will be either looked up
              or created.
            - I{'lookup'} means the object MUST already exist configFS.
            - I{'create'} means the object must NOT already exist in configFS.
        @type mode:string
        @return: A Host object.
        '''
        super().__init__()

        self.nqn = nqn
        self._path = f"{self.configfs_dir}/hosts/{nqn}"
        self._create_in_cfs(mode)

    @classmethod
    def setup(cls, t, err_func):
        '''
        Set up Host objects based upon t dict, from saved config.
        Guard against missing or bad dict items, but keep going.
        Call 'err_func' for each error.
        '''

        if 'nqn' not in t:
            err_func("'nqn' not defined for Host")
            return

        try:
            Host(t['nqn'])
        except CFSError as e:
            err_func(f"Could not create Host object: {e}")
            return

    def dump(self):
        '''
        Returns a dict with the config of the object.
        '''
        d = super().dump()
        d['nqn'] = self.nqn
        return d


def _test():
    from doctest import testmod
    testmod()


if __name__ == "__main__":
    _test()
